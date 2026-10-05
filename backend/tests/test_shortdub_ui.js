// Built-in Node tests only; no website, AI, database or payment calls.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const account = fs.readFileSync(path.join(root, 'account.html'), 'utf8');

function excerpt(source, first, last) {
    const start = source.lastIndexOf(first);
    assert.ok(start >= 0, first);
    const end = source.indexOf(last, start + first.length);
    assert.ok(end > start, last);
    return source.slice(start, end);
}

function context() {
    const elements = new Map();
    const element = () => ({ disabled: false, textContent: '', classList: { add() {}, remove() {} } });
    const messages = [], calls = [], confirmations = [];
    const c = vm.createContext({
        window: { currentLang: 'en', clonedVoiceIds: [] },
        document: { getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); }, querySelector() { return null; } },
        segmentsData: [{ segment_id: 'seg_0', arabic_text: 'مرحبا', emotion: 'neutral', speaker: 'Speaker 1', start: 0 }],
        speakerVoices: { 'Speaker 1': 'voice' }, currentJobId: 'a'.repeat(32), totalDuration: 4,
        generatePollTimer: null, checkGenerateProgress() {}, clearInterval() {}, setInterval() { return 1; },
        fetchUsage() {}, refreshCredits() {}, notify(type, text) { messages.push({ type, text }); },
        LisanDialog: { async confirm(message) { confirmations.push(message); return c.accept; } },
        accept: true, quote: { credits: 114, voice_credits: 10, analysis_credits: 104, required_balance: 114 },
        async fetch(url, options) {
            const payload = JSON.parse(options.body);
            calls.push({ url, payload });
            if (url.endsWith('/quote')) return { ok: true, async json() { return c.quote; } };
            return { ok: true, async json() { return url.endsWith('regenerate_line') ? { status: 'success', credits_charged: 2 } : { status: 'started' }; } };
        }
    });
    elements.set('ttsProvider', { value: 'elevenlabs' });
    elements.set('geminiVoice', { value: 'Kore' });
    elements.set('durationMode', { value: 'exact' });
    vm.runInContext(excerpt(app, 'async function confirmShortPrice(', '// Pure editing action'), c);
    return { c, calls, messages, confirmations, elements };
}

test('refunds display a green positive amount, spending displays a red negative amount', () => {
    const c = vm.createContext({});
    vm.runInContext(excerpt(account, 'function creditChange(', 'function safeMsg('), c);
    assert.equal(c.creditChange(-3).text, '+3');
    assert.equal(c.creditChange(-3).color, '#059669');
    assert.equal(c.creditChange(3).text, '−3');
    assert.equal(c.creditChange(3).color, '#dc2626');
    assert.equal(c.creditChange(0).text, '0');
    assert.equal(c.creditChange('garbage').text, '—');
});

test('canceling the generation price check starts no paid request and unlocks the button', async () => {
    const { c, calls, elements } = context();
    c.accept = false;
    await c.generateAudio();
    assert.deepEqual(calls.map(x => x.url), ['/api/generate/quote']);
    assert.equal(elements.get('generateButton').disabled, false);
});

test('generation displays all 114 credits including analysis and submits the confirmed price', async () => {
    const { c, calls, confirmations } = context();
    await c.generateAudio();
    assert.match(confirmations[0], /Cost: 114 credits/);
    assert.match(confirmations[0], /not yet charged: 104 credits/);
    assert.equal(calls[1].payload.accepted_credits, 114);
    assert.equal(calls[1].url, '/api/generate');
});

test('an unavailable or invalid quote prevents generation', async () => {
    for (const quote of [{ error: 'Try again' }, { credits: -1 }, { credits: '10' }]) {
        const { c, calls, elements } = context();
        c.quote = quote;
        await c.generateAudio();
        assert.equal(calls.length, 1);
        assert.equal(elements.get('generateButton').disabled, false);
    }
});

test('text edits during confirmation do not change the paid submission', async () => {
    const { c, calls } = context();
    c.LisanDialog.confirm = async () => { c.segmentsData[0].arabic_text = 'different text'; return true; };
    await c.generateAudio();
    assert.equal(calls[1].payload.segments[0].arabic_text, 'مرحبا');
});

test('canceling line regeneration starts no paid request', async () => {
    const { c, calls } = context();
    c.accept = false;
    const button = { disabled: false, textContent: '' };
    await c.regenerateLine(0, button);
    assert.deepEqual(calls.map(x => x.url), ['/api/regenerate_line/quote']);
    assert.equal(button.disabled, false);
});

test('regeneration reports the actual server charge and confirms it first', async () => {
    const { c, calls, messages } = context();
    c.quote = { credits: 2, voice_credits: 2, analysis_credits: 0, required_balance: 2 };
    await c.regenerateLine(0, { disabled: false, textContent: '' });
    assert.equal(calls[1].payload.accepted_credits, 2);
    assert.ok(messages.some(m => m.type === 'success' && m.text.includes('Cost: 2 credits.')));
});

test('Arabic confirmations include the quote and analysis breakdown', async () => {
    const { c, confirmations } = context();
    c.window.currentLang = 'ar';
    await c.generateAudio();
    assert.match(confirmations[0], /114 رصيد/);
    assert.match(confirmations[0], /104 رصيد/);
});

test('Arabic regeneration success reports the actual charge', async () => {
    const { c, messages } = context();
    c.window.currentLang = 'ar';
    c.quote = { credits: 2, voice_credits: 2, analysis_credits: 0, required_balance: 2 };
    await c.regenerateLine(0, { disabled: false, textContent: '' });
    assert.ok(messages.some(m => m.type === 'success' && m.text.includes('التكلفة: 2 رصيد')));
});

test('invalid project JSON is described as a file problem and does not replace the current project', () => {
    const notifications = [];
    const current = [{ segment_id: 'existing' }];
    const c = vm.createContext({ window: { currentLang: 'en' }, segmentsData: current,
        notify(type, text) { notifications.push(text); },
        FileReader: class { readAsText() { this.result = '{bad json'; this.onload(); } }
    });
    vm.runInContext(excerpt(app, 'function loadProjectFile(', '// Cloned/custom voices'), c);
    c.loadProjectFile({ target: { files: [{}], value: 'test' } });
    assert.equal(c.segmentsData, current);
    assert.match(notifications[0], /valid Lisan AI project/);
    assert.doesNotMatch(notifications[0], /Connection problem|Load failed/);
});

test('canceling a merge confirms its full music budget and makes no paid request', async () => {
    const {c,calls,confirmations,elements}=context();
    c.quote={max_total:21,music_max:20};c.accept=false;c.subsText=(en)=>en;c.window.LisanDialog=c.LisanDialog;
    vm.runInContext(excerpt(app,'async function mergeVideo()', '\nasync function'),c);
    await c.mergeVideo();
    assert.deepEqual(calls.map(x=>x.url),['/api/merge_video/quote']);
    assert.match(confirmations[0],/21 credits/);assert.match(confirmations[0],/20 for music/);
    assert.equal(elements.get('mergeButton').disabled,false);
});

test('an invalid merge quote cannot start paid processing', async () => {
    for(const quote of [{max_total:-1,music_max:0},{max_total:'21',music_max:20},{max_total:1,music_max:10}]) {
        const {c,calls,confirmations,elements}=context();c.quote=quote;c.subsText=(en)=>en;
        vm.runInContext(excerpt(app,'async function mergeVideo()', '\nasync function'),c);
        await c.mergeVideo();assert.equal(calls.length,1);assert.equal(confirmations.length,0);
        assert.equal(elements.get('mergeButton').disabled,false);
    }
});
