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
        window: { currentLang: 'en', clonedVoiceIds: [], crypto: require('node:crypto').webcrypto },
        localStorage: (() => { const data=new Map(); return {getItem:key=>data.get(key)||null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)}; })(),
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
    vm.runInContext(excerpt(app, 'function shortWaqfMode(', '\nfunction saveProject('), c);
    vm.runInContext(excerpt(app, 'function shortGeneratePayload(', '// Pure editing action'), c);
    return { c, calls, messages, confirmations, elements };
}

test('refunds display a plain positive refunded amount, spending displays a red negative amount', () => {
    const c = vm.createContext({});
    vm.runInContext(excerpt(account, 'function creditChange(', 'function safeMsg('), c);
    assert.equal(c.creditChange(-3).text, '3 refunded');
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
        subsText(en, ar) { return c.window.currentLang === "ar" ? ar : en; },
        FileReader: class { readAsText() { this.result = '{bad json'; this.onload(); } }
    });
    vm.runInContext(excerpt(app, 'function shortWaqfMode(', '\nfunction saveProject('), c);
    vm.runInContext(excerpt(app, 'function loadProjectFile(', '// Cloned/custom voices'), c);
    c.loadProjectFile({ target: { files: [{}], value: 'test' } });
    assert.equal(c.segmentsData, current);
    assert.match(notifications[0], /another saved Lisan AI project/);
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
    assert.equal(nodes.filter(n=>n.tag==='select').length,2);          // the voice list and the optional age choice
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

function waqfEditor() {
    const { c, calls, messages } = context();
    const wrappers = [];
    class Element {
        constructor(tag) { this.tagName = tag; this.children = []; this.style = {}; this.attributes = {}; this.value = ''; this.textContent = ''; }
        appendChild(child) { this.children.push(child); if (child.className === 'sd-waqf') wrappers.push(child); return child; }
        setAttribute(name, value) { this.attributes[name] = value; }
        get options() { return this.children.filter(child => child.tagName === 'option'); }
        querySelector(tag) { for (const child of this.children) { if (child.tagName === tag) return child; const nested = child.querySelector(tag); if (nested) return nested; } return null; }
    }
    c.document.createElement = tag => new Element(tag);
    c.document.querySelectorAll = () => wrappers;
    c.updateBadges = () => { c.badgeUpdates = (c.badgeUpdates || 0) + 1; };
    c.getSpeakerOptionsList = () => ['Speaker 1']; c.EMOTIONS = ['neutral']; c.EXTRA_STYLE_TAGS = [];
    vm.runInContext(excerpt(app, 'function shortWaqfLabels(', '// ===== CLONE ANALYSIS'), c);
    return { c, calls, messages, wrappers, Element };
}

test('short editor builds the matching picker and changing it keeps Arabic text intact with no request', () => {
    const { c, calls } = waqfEditor();
    const seg = c.segmentsData[0]; seg.arabic_text = 'شُكْرًا!';
    const row = c.createRow(seg, 0), select = row.querySelector('textarea').tagName;
    assert.equal(select, 'textarea');
    const wrap = row.children[6].children.find(child => child.className === 'sd-waqf');
    assert.ok(wrap, 'picker follows the Arabic textarea');
    const picker = wrap.querySelector('select');
    assert.equal(picker.value, 'auto'); assert.equal(seg.waqf, 'auto');
    assert.deepEqual(picker.options.map(o => [o.value, o.textContent]), [['auto','Auto'],['stop','Stop'],['join','Join']]);
    for (const mode of ['stop','join','auto']) { picker.value = mode; picker.onchange(); assert.equal(seg.waqf, mode); }
    assert.equal(seg.arabic_text, 'شُكْرًا!'); assert.equal(c.badgeUpdates, 3); assert.equal(calls.length, 0);
});

test('picker translates existing controls without changing their choice or rebuilding text inputs', () => {
    const { c, wrappers } = waqfEditor(); c.segmentsData[0].waqf = 'join';
    const row = c.createRow(c.segmentsData[0], 0), input = row.children[6].children[0]; input.value = 'unsaved edit';
    c.window.currentLang = 'ar'; c.updateShortWaqfLabels();
    assert.equal(wrappers[0].querySelector('select').value, 'join');
    assert.deepEqual(wrappers[0].querySelector('select').options.map(o => o.textContent), ['تلقائي','وقف','وصل']);
    assert.equal(wrappers[0].querySelector('select').attributes['aria-label'], 'نهاية السطر');
    assert.equal(input.value, 'unsaved edit');
    c.window.currentLang = 'en'; c.updateShortWaqfLabels();
    assert.equal(wrappers[0].querySelector('span').textContent, 'Line ending');
});

test('generation quote and submission carry every line ending and leave the source text untouched', async () => {
    const { c, calls } = context();
    c.segmentsData = ['auto','stop','join',undefined,'invalid'].map((waqf, i) => ({segment_id:'seg_'+i,arabic_text:'شُكْرًا.',speaker:'Speaker 1',waqf}));
    const original = JSON.stringify(c.segmentsData);
    await c.generateAudio();
    for (const call of calls) assert.deepEqual(call.payload.segments.map(s => s.waqf), ['auto','stop','join','auto','auto']);
    assert.equal(JSON.stringify(c.segmentsData), original);
});

test('re-speaking uses the selected ending and all neighboring endings in its confirmed snapshot', async () => {
    for (const mode of ['auto','stop','join',undefined]) {
        const { c, calls } = context(); c.segmentsData[0].waqf = mode;
        c.segmentsData.push({segment_id:'next',arabic_text:'أهلًا.',speaker:'Speaker 1',waqf:'join'});
        c.beforeQuote = () => { c.segmentsData[0].waqf = 'changed after quote'; };
        await c.regenerateLine(0, {disabled:false,textContent:''});
        assert.equal(calls.length, 2);
        for (const call of calls) { assert.equal(call.payload.segment.waqf, mode || 'auto'); assert.equal(call.payload.segments[0].waqf, mode || 'auto'); assert.equal(call.payload.segments[1].waqf, 'join'); assert.equal(call.payload.segment.arabic_text, 'مرحبا'); }
    }
});

test('project save and load preserve all choices and old projects default to auto', () => {
    const { c } = context(); let saved;
    c.originalSegments = []; c.isVideoUpload = false; c.speakerVoiceNames = {}; c.speakerChoices = {}; c.clonedBySpeaker = {};
    c.downloadText = (name, text) => { saved = JSON.parse(text); };
    vm.runInContext(excerpt(app, 'function saveProject(', '// Cloned/custom voices'), c);
    c.segmentsData = ['auto','stop','join',undefined,'bad'].map((waqf,i) => ({segment_id:'seg_'+i,arabic_text:'جِدًّا.',waqf}));
    const original = JSON.stringify(c.segmentsData); c.saveProject();
    assert.deepEqual(saved.segments.map(s => s.waqf), ['auto','stop','join','auto','auto']);
    assert.equal(JSON.stringify(c.segmentsData), original);
    c.FileReader = class { readAsText() { this.result = JSON.stringify(saved); this.onload(); } };
    c.renderTable = c.renderSpeakerVoices = c.validateClonedVoicesAfterLoad = c.showMediaBanner = () => {};
    c.loadProjectFile({target:{files:[{}],value:'project'}});
    assert.deepEqual(Array.from(c.segmentsData, s => s.waqf), ['auto','stop','join','auto','auto']);
    assert.ok(c.segmentsData.every(s => s.arabic_text === 'جِدًّا.'));
    saved = {segments:[{arabic_text:'أَهْلًا.'}]}; c.loadProjectFile({target:{files:[{}],value:'old'}});
    assert.equal(c.segmentsData[0].waqf, 'auto'); assert.equal(c.segmentsData[0].arabic_text, 'أَهْلًا.');
});

test('refund labels handle both record signs and Arabic without mislabeling an admin grant', () => {
    const c = vm.createContext({LANG:'en'});
    vm.runInContext(excerpt(account, 'function creditChange(', 'function safeMsg('), c);
    for (const amount of [3, -3]) {
        assert.equal(c.creditChange(amount, 'long_dub_refund').text, '3 refunded');
        assert.equal(c.creditChange(amount, 'long_dub_refund').color, '#059669');
    }
    assert.equal(c.creditChange(-3, 'admin_adjustment').text, '+3');
    c.LANG = 'ar'; assert.equal(c.creditChange(-3, 'long_dub_refund').text, '3 مستردّ');
    assert.equal(c.creditChange(3, 'generate').text, '−3');
    assert.ok(account.includes('creditChange(s.credits, s.action)'));
});

test('damaged project files and read errors give one clear sentence and keep the current workspace', () => {
    for (const lang of ['en','ar']) {
        for (const fixture of ['{bad','null','{}','{"segments":[null]}','{"segments":[5]}','{"segments":[[]]}','read error','read throws']) {
            const { c, messages } = context(); c.window.currentLang = lang;
            const current = c.segmentsData; c.workspaceHasMedia = true;
            c.projectWasLoaded = false; let banners = 0; c.showMediaBanner = () => { banners++; };
            c.FileReader = class { readAsText() { if (fixture === 'read throws') throw new Error('read failed'); if (fixture === 'read error') { this.onerror(); return; } this.result = fixture; this.onload(); } };
            vm.runInContext(excerpt(app, 'function loadProjectFile(', '// Cloned/custom voices'), c);
            c.loadProjectFile({target:{files:[{}],value:'bad'}});
            assert.equal(c.segmentsData, current); assert.equal(c.workspaceHasMedia, true); assert.equal(c.projectWasLoaded, false); assert.equal(banners, 0);
            assert.equal(messages.length, 1); assert.equal(messages[0].type, 'error');
            assert.match(messages[0].text, lang === 'ar' ? /اختر ملف مشروع محفوظًا آخر/ : /choose another saved Lisan AI project/);
            assert.equal((messages[0].text.match(/[.!?](?=\s|$)/g) || []).length, 1);
        }
    }
});

test('only successful project reads disable media and show the loaded-project banner', () => {
    const { c, messages } = context(); let reader, banners = 0;
    c.workspaceHasMedia = true; c.originalSegments = []; c.isVideoUpload = false;
    c.FileReader = class { readAsText() { reader = this; } };
    c.showMediaBanner = () => { banners++; }; c.renderTable = c.renderSpeakerVoices = c.validateClonedVoicesAfterLoad = () => {};
    vm.runInContext(excerpt(app, 'function loadProjectFile(', '// Cloned/custom voices'), c);
    c.loadProjectFile({target:{files:[{}],value:'good'}});
    assert.equal(c.workspaceHasMedia, true); assert.equal(banners, 0); assert.equal(messages.length, 0);
    reader.result = JSON.stringify({segments:[{segment_id:'loaded',arabic_text:'مرحبا',waqf:'stop'}]}); reader.onload();
    assert.equal(c.workspaceHasMedia, false); assert.equal(banners, 1);
    assert.equal(c.segmentsData[0].waqf, 'stop'); assert.equal(messages.filter(m => m.type === 'success').length, 1);
    assert.doesNotMatch(app, /loadProjectFile = function \(ev\)/);
});

test('timeline help is short, translated and contains no vendor names or technical wording', () => {
    const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
    const note = html.match(/<p[^>]*data-tip-host="tlTitle"[^>]*>([^<]*)<\/p>/)[1];
    assert.ok(note.length < 160); assert.match(note, /Play to listen/); assert.match(note, /apply your changes/);
    assert.doesNotMatch(note, /ElevenLabs|Gemini|Inworld|Fal|MP3|segments|rebuild/i);
    const pair = app.match(/\["Play to listen[^\n]+/)[0];
    const texts = JSON.parse(pair.replace(/,\s*$/,''));
    assert.equal(texts[0], note); assert.match(texts[1], /طبّق التغييرات/);
    assert.doesNotMatch(texts[1], /ElevenLabs|Gemini|Inworld|Fal|MP3|مقاطع|إعادة بناء/i);
});
