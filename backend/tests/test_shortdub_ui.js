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
        setBadge() {}, subsText(en, ar) { return c.window.currentLang === "ar" ? ar : en; },
        fetchUsage() {}, refreshCredits() {}, notify(type, text) { messages.push({ type, text }); },
        LisanDialog: { async confirm(message) { confirmations.push(message); return c.accept; } },
        accept: true, quote: { credits: 114, voice_credits: 10, analysis_credits: 104, required_balance: 114 },
        async fetch(url, options) {
            const payload = JSON.parse(options.body);
            calls.push({ url, payload });
            if (url.endsWith('/quote')) return { ok: true, async json() { if(c.beforeQuote)c.beforeQuote(); return c.quote; } };
            return { ok: true, async json() { return url.endsWith('regenerate_line') ? { status: 'success', credits_charged: 2 } : { status: 'started' }; } };
        }
    });
    elements.set('ttsProvider', { value: 'elevenlabs' });
    elements.set('geminiVoice', { value: 'Kore' });
    elements.set('durationMode', { value: 'exact' });
    vm.runInContext(excerpt(app, 'function shortGeneratePayload(', '// Pure editing action'), c);
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

test('generation checks the complete price and starts without a confirmation dialog', async () => {
    const { c, calls, confirmations } = context();
    c.accept = false;
    await c.generateAudio();
    assert.deepEqual(calls.map(x => x.url), ['/api/generate/quote','/api/generate']);
    assert.equal(confirmations.length,0);
    assert.equal(calls[1].payload.accepted_credits,114);
});

test('generation notification includes all 114 credits, including unsettled analysis', async () => {
    const { c, calls, messages } = context();
    await c.generateAudio();
    assert.ok(messages.some(m => m.text.includes('Cost: 114 credits.')));
    assert.equal(calls[1].payload.accepted_credits,114);
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

test('text edits during the price request do not change the paid submission', async () => {
    const { c, calls } = context();
    c.beforeQuote = () => { c.segmentsData[0].arabic_text = 'different text'; };
    await c.generateAudio();
    assert.equal(calls[1].payload.segments[0].arabic_text, 'مرحبا');
});

test('an invalid single-line quote makes no paid request and unlocks the control', async () => {
    const { c, calls } = context();c.quote={credits:-1};
    const button={disabled:false,textContent:''};await c.regenerateLine(0,button);
    assert.deepEqual(calls.map(x => x.url),['/api/regenerate_line/quote']);assert.equal(button.disabled,false);
});

test('single-line regeneration has no modal, even above one credit, and reports the server charge', async () => {
    const { c, calls, messages, confirmations }=context();c.quote={credits:20};
    await c.regenerateLine(0,{disabled:false,textContent:''});
    assert.equal(confirmations.length,0);assert.equal(calls[1].payload.accepted_credits,20);
    assert.ok(messages.some(m => m.type==='success'&&m.text.includes('Cost: 2 credits.')));
});

test('Arabic generation notification shows its numeric price without a confirmation', async () => {
    const { c, messages, confirmations }=context();c.window.currentLang='ar';await c.generateAudio();
    assert.equal(confirmations.length,0);assert.ok(messages.some(m => m.text.includes('114 رصيد')));
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

test('a merge that costs more than the price on the button is not started; the button shows the new price', async () => {
    const {c,calls,confirmations,messages}=context();
    c.quote={max_total:21,music_max:20};c.subsText=(en)=>en;c.window._mergeShownTotal=2;
    vm.runInContext(excerpt(app,'function mergeStatusText(', '\nasync function mergeVideo()'),c);
    vm.runInContext(excerpt(app,'async function mergeVideo()', '\nasync function'),c);
    c.window._mergeShownTotal=2;
    await c.mergeVideo();
    assert.deepEqual(calls.map(x=>x.url),['/api/merge_video/quote']);
    assert.equal(confirmations.length,0);
    assert.equal(c.window._mergeShownTotal,21);
    assert.match(messages.at(-1).text,/21 credits/);
});

test('a merge at the price shown on the button starts at once, with no confirmation box', async () => {
    const {c,calls,confirmations,elements}=context();
    c.quote={max_total:32,music_max:30};c.subsText=(en)=>en;
    vm.runInContext(excerpt(app,'function mergeStatusText(', '\nasync function mergeVideo()'),c);
    vm.runInContext(excerpt(app,'async function mergeVideo()', '\nasync function'),c);
    c.window._mergeShownTotal=32;
    await c.mergeVideo();
    assert.deepEqual(calls.map(x=>x.url),['/api/merge_video/quote','/api/merge_video']);
    assert.equal(confirmations.length,0);
    assert.equal(calls[1].payload.accepted_credits,32);
    assert.match(elements.get('mergePriceNote').textContent,/up to 32 credits, including up to 30 for music inpainting. Only successful repairs are charged./);
});

test('a click before any price was shown only shows the price', async () => {
    const {c,calls}=context();
    c.quote={max_total:2,music_max:0};c.subsText=(en)=>en;
    vm.runInContext(excerpt(app,'function mergeStatusText(', '\nasync function mergeVideo()'),c);
    vm.runInContext(excerpt(app,'async function mergeVideo()', '\nasync function'),c);
    c.window._mergeShownTotal=null;
    await c.mergeVideo();
    assert.deepEqual(calls.map(x=>x.url),['/api/merge_video/quote']);
    assert.equal(c.window._mergeShownTotal,2);
});

test('an invalid merge quote cannot start paid processing', async () => {
    for(const quote of [{max_total:-1,music_max:0},{max_total:'21',music_max:20},{max_total:1,music_max:10}]) {
        const {c,calls,confirmations,elements}=context();c.quote=quote;c.subsText=(en)=>en;
        vm.runInContext(excerpt(app,'function mergeStatusText(', '\nasync function mergeVideo()'),c);vm.runInContext(excerpt(app,'async function mergeVideo()', '\nasync function'),c);
        await c.mergeVideo();assert.equal(calls.length,1);assert.equal(confirmations.length,0);
        assert.equal(elements.get('mergeButton').disabled,false);
    }
});

test('another generation click during its quote request cannot submit twice', async () => {
    const {c,calls}=context();var release;const gate=new Promise(resolve=>{release=resolve;});var original=c.fetch;
    c.fetch=async function(url,options){if(url==='/api/generate/quote')await gate;return original(url,options);};
    var first=c.generateAudio();await c.generateAudio();release();await first;
    assert.equal(calls.filter(x=>x.url==='/api/generate').length,1);
});

function priceContext() {
    const result = context(), timers = new Map(), badges = [];
    let sequence = 0;
    result.c.setTimeout = callback => { timers.set(++sequence, callback); return sequence; };
    result.c.clearTimeout = id => timers.delete(id);
    result.c.setBadge = (id, value) => badges.push(value);
    result.timers = timers; result.badges = badges;
    result.runTimer = async () => {
        const [id, callback] = timers.entries().next().value;
        timers.delete(id); await callback();
    };
    return result;
}

test('repeated unchanged updates retain the pending numeric price request', async () => {
    const p = priceContext();
    p.c.scheduleGeneratePrice(); p.c.scheduleGeneratePrice(); p.c.scheduleGeneratePrice();
    assert.equal(p.timers.size, 1);
    await p.runTimer();
    assert.deepEqual(p.badges, ['…', 114]);
    assert.deepEqual(p.calls.map(x => x.url), ['/api/generate/quote']);
});

test('a late quote cannot replace the price of newly edited text', async () => {
    const p = priceContext(); let release;
    const gate = new Promise(resolve => { release = resolve; });
    p.c.fetch = async (url, options) => {
        const text = JSON.parse(options.body).segments[0].arabic_text;
        if (text === 'مرحبا') await gate;
        return { ok: true, async json() { return { credits: text === 'مرحبا' ? 114 : 7 }; } };
    };
    p.c.scheduleGeneratePrice(); const earlier = p.runTimer();
    p.c.segmentsData[0].arabic_text = 'new translation';
    p.c.scheduleGeneratePrice(); await p.runTimer(); release(); await earlier;
    assert.equal(p.badges.at(-1), 7);
    assert.equal(p.badges.includes(114), false);
});

test('an unavailable price displays a dash and can be requested again', async () => {
    const p = priceContext(); p.c.quote = { credits: '114' };
    p.c.scheduleGeneratePrice(); await p.runTimer();
    assert.equal(p.badges.at(-1), '—');
    p.c.quote = { credits: 6 }; p.c.scheduleGeneratePrice(); await p.runTimer();
    assert.equal(p.badges.at(-1), 6);
});

test('choosing the first voice and programmatic auto-assignment refresh the generation price', async () => {
    const p = priceContext(), nodes = [];
    const node = () => ({style:{},children:[],appendChild(child){this.children.push(child);},innerHTML:''});
    const tbody = node();
    p.c.document.querySelector = () => tbody;
    p.c.document.createElement = tag => { const item=node(); item.tag=tag; nodes.push(item); return item; };
    Object.assign(p.c, {voicePools:{male:[{voice_id:'v1'}],female:[]},clonedBySpeaker:{},speakerChoices:{},speakerVoiceNames:{},speakerVoices:{},
        ensureVoicePools:async()=>true, applyChoice(name){p.c.speakerVoices[name]='v1';}});
    vm.runInContext(excerpt(app,'var speakerGenderPick = {};', 'function updateSpeakerName('),p.c);
    vm.runInContext(excerpt(app,'async function renderSpeakerVoices()', 'async function ensureVoicePools()'),p.c);
    await p.c.renderSpeakerVoices(); assert.equal(p.badges.at(-1),'—');
    // Gender is two radio buttons in the voice cell; the speaker cell holds exactly the name (older patches that add
    // custom, cloned and saved voices find the row and its dropdown that way).
    assert.equal(nodes.filter(n=>n.tag==='input'&&n.type==='radio').length,2);
    assert.equal(nodes.filter(n=>n.tag==='td')[0].textContent,'Speaker 1');
    assert.equal(nodes.filter(n=>n.tag==='select').length,1);
    const select=nodes.find(n=>n.tag==='select'); select.value='male:1'; select.onchange();
    assert.equal(p.timers.size,1); await p.runTimer(); assert.equal(p.badges.at(-1),114);
    p.c.speakerVoices={};p.c.speakerChoices={};p.c.scheduleGeneratePrice();
    vm.runInContext(excerpt(app,'async function autoAssignVoices()', '// ===== VOICE LIBRARY BROWSER'),p.c);
    await p.c.autoAssignVoices();await p.runTimer();assert.equal(p.badges.at(-1),114);
});

test('a merge whose music cannot be rebuilt starts at once, with no confirmation, and says so afterwards', async () => {
    const {c,calls,confirmations,messages}=context();
    c.quote={max_total:2,music_max:0,music_kept:false};c.subsText=(en)=>en;
    vm.runInContext(excerpt(app,'function mergeStatusText(', '\nasync function mergeVideo()'),c);
    vm.runInContext(excerpt(app,'async function mergeVideo()', '\nasync function'),c);
    c.window._mergeShownTotal=2;
    await c.mergeVideo();
    assert.deepEqual(calls.map(x=>x.url),['/api/merge_video/quote','/api/merge_video']);
    assert.equal(confirmations.length,0);
    assert.equal(calls[1].payload.accepted_credits,2);
});

test('the merge progress text is translated into Arabic and keeps the part numbers', () => {
    const {c}=context();
    vm.runInContext(excerpt(app,'function mergeStatusText(', '\nasync function mergeVideo()'),c);
    c.window.currentLang='ar';
    assert.match(c.mergeStatusText('Rebuilding the background music under the speech: part 2 of 3. This takes about a minute per part...'),/2 من 3/);
    c.window.currentLang='en';
    assert.equal(c.mergeStatusText('Mixing the dubbed voice with the background...'),'Mixing the dubbed voice with the background...');
});

test('a speaker has one gender: Step 1.5 sets it for all lines, Step 4 can change it and swaps a voice of the other gender', () => {
    const c=vm.createContext({window:{currentLang:'en'},subsText(en){return en;},scheduleGeneratePrice(){},
        segmentsData:[{speaker:'Ali',gender:'male'},{speaker:'Sara',gender:'male'},{speaker:'Sara',gender:'male'}],
        speakerChoices:{Sara:'male:1'},speakerVoices:{Sara:'v1'},speakerVoiceNames:{Sara:'x'},
        voicePools:{male:[{voice_id:'v1'}],female:[{voice_id:'v2'}]},
        applyChoice(name){const m=c.speakerChoices[name].split(':');c.speakerVoices[name]=c.voicePools[m[0]][+m[1]-1].voice_id;}});
    vm.runInContext(excerpt(app,'var speakerGenderPick = {};', 'function updateSpeakerName('),c);
    assert.equal(c.speakerGenderOf('Ali'),'male');
    c.setSpeakerGender('Sara','female');
    assert.deepEqual(c.segmentsData.map(s=>s.gender),['male','female','female']);
    assert.equal(c.speakerGenderOf('Sara'),'female');
    assert.equal(c.speakerChoices.Sara,'female:1');
    assert.equal(c.speakerVoices.Sara,'v2');
});
