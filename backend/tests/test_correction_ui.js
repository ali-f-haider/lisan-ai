// Runs actual client handlers with local doubles; no website, payment or AI calls.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const client = fs.readFileSync(path.join(__dirname, '..', 'dub_long_edit.js'), 'utf8');

function excerpt(first, last) {
    const start = client.indexOf(first), end = client.indexOf(last, start + first.length);
    assert.ok(start >= 0 && end > start, 'Client section exists: ' + first);
    return client.slice(start, end);
}

function element(tag = 'div') {
    const classes = new Set(), listeners = new Map();
    return {
        tagName: tag, children: [], attributes: {}, style: {}, disabled: false,
        textContent: '', value: '', paused: true, currentTime: 0, isConnected: true,
        classList: { add(v) { classes.add(v); }, remove(v) { classes.delete(v); }, contains(v) { return classes.has(v); } },
        append(...items) { this.children.push(...items); },
        appendChild(item) { this.append(item); return item; },
        setAttribute(name, value) { this.attributes[name] = value; },
        addEventListener(name, callback) {
            if (!listeners.has(name)) listeners.set(name, []);
            listeners.get(name).push(callback);
        },
        dispatch(name) { for (const callback of listeners.get(name) || []) callback(); },
        click() { return this.onclick && this.onclick(); },
        play() { this.paused = false; this.dispatch('play'); return Promise.resolve(); },
        pause() { this.paused = true; this.dispatch('pause'); },
        remove() { this.isConnected = false; }
    };
}

function harness() {
    const elements = new Map(), created = [], calls = [], timers = new Map(), messages = [], confirmations = [];
    let timerId = 0;
    const get = id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
    const control = element('textarea');
    const lisanPlayer = { isOpen() { return false; }, open(options) { c.shared.push(options); } };
    const c = vm.createContext({
        lang: 'en', project: { id: 'parent' }, state: { busy: false, history: [], segments: [], duration: 60, speaker_list: [{ id: 's1', name: 'Sam' }] },
        meta: { parent: { size: 1000, has_video: true } }, listen: null, shared: [],
        LisanPlayer: lisanPlayer, window: { LisanPlayer: lisanPlayer },
        error: ex => { c.lastError = ex; }, lastError: null,
        selected: new Set(['line-1']), notified: new Set(), working: false, pollTimer: null, dirty: false,
        $: get, tr: (en, ar) => c.lang === 'ar' ? ar : en,
        document: {
            body: element('body'),
            createElement(tag) { const node = element(tag); created.push(node); return node; },
            querySelectorAll() { return [control]; }, addEventListener() {}, removeEventListener() {}
        },
        URL: { createObjectURL() { return 'blob:local-test'; }, revokeObjectURL() {} },
        setTimeout(callback, delay) { timers.set(++timerId, { callback, delay }); return timerId; },
        clearTimeout(id) { timers.delete(id); },
        async save() {}, renderLines() {}, renderHistory() {},
        notify(type, message) { messages.push({ type, message }); },
        LisanDialog: { async confirm(message) { confirmations.push(message); return true; } },
        respond: async url => {
            if (url.endsWith('/quote')) return { max_total: 7, token: 'verified-price' };
            if (url.endsWith('/dub')) return { id: 'pass-1', status: 'confirmed', percent: 0, message: 'Queued' };
            if (url.endsWith('/corrections')) return { busy: true, history: [{ id: 'pass-1', status: 'dubbing', percent: 40, message: 'Generating selected lines' }] };
            if (url.endsWith('/player')) return { url: '/api/longdub/parent/media', kind: 'audio' };
            if (url === '/api/longdub') return { credits: 93 };
            throw new Error('Unexpected request: ' + url);
        },
        async fetch(url, options) {
            calls.push({ url, method: options.method, body: options.body && JSON.parse(options.body) });
            const data = await c.respond(url, options);
            return { ok: true, async json() { return data; } };
        }
    });
    vm.runInContext(excerpt('  async function api(', '  function notify('), c);
    vm.runInContext(excerpt('  function setBusy(', '  function count('), c);
    vm.runInContext(excerpt('  function statusText(', '  function renderHistory('), c);
    vm.runInContext(excerpt('  function progress(', "  $('finish').onclick="), c);
    vm.runInContext(excerpt('  function button(', '  function refreshOverlaps('), c);
    vm.runInContext(excerpt('  function openPlayer(', "  $('projects').onchange="), c);
    return {
        c, elements, get, control, created, calls, timers, messages, confirmations,
        async runTimer() {
            assert.equal(timers.size, 1, 'Exactly one progress retry is scheduled');
            const [id, timer] = timers.entries().next().value;
            timers.delete(id); assert.equal(timer.delay, 3000);
            await timer.callback();
            // Timer handlers do not return their promise; drain local request microtasks.
            await new Promise(setImmediate);
        }
    };
}

test('a paid correction keeps polling when the independent balance refresh fails', async () => {
    const h = harness(), normal = h.c.respond;
    h.c.respond = async url => {
        if (url === '/api/longdub') throw new Error('Balance temporarily unavailable');
        return normal(url);
    };
    await h.get('dub').onclick();
    assert.equal(h.calls.filter(call => call.url.endsWith('/dub')).length, 1);
    assert.equal(h.calls.filter(call => call.url.endsWith('/corrections')).length, 1);
    assert.equal(h.confirmations.length, 0, 'A single selected line needs no modal');
    assert.equal(h.timers.size, 1, 'Job progress continues despite balance failure');
    assert.equal(h.get('balance').textContent, '…');
    assert.equal(h.get('progressBar').attributes['aria-valuenow'], '40');
    assert.equal(h.get('dub').disabled, true, 'A running paid job cannot be started again');
});

test('a transient progress failure retries and completes without a second paid submission', async () => {
    const h = harness(), normal = h.c.respond;
    let attempts = 0;
    h.c.respond = async url => {
        if (!url.endsWith('/corrections')) return normal(url);
        attempts++;
        if (attempts === 1) throw new Error('Temporary connection loss');
        if (attempts === 2) return normal(url);
        return { busy: false, history: [{ id: 'pass-1', status: 'done', percent: 100, message: 'Ready', paid: { dub: 5, music: 2 } }] };
    };
    await h.get('dub').onclick();
    assert.equal(h.timers.size, 1); assert.equal(h.get('dub').disabled, true);
    await h.runTimer(); await h.runTimer();
    assert.equal(attempts, 3);
    assert.equal(h.calls.filter(call => call.url.endsWith('/quote')).length, 1);
    assert.equal(h.calls.filter(call => call.url.endsWith('/dub')).length, 1);
    assert.equal(h.timers.size, 0); assert.equal(h.get('dub').disabled, false);
    assert.equal(h.control.disabled, false); assert.equal(h.get('error').textContent, '');
    assert.equal(h.get('progressBar').attributes['aria-valuenow'], '100');
    assert.equal(h.messages.filter(message => message.type === 'success').length, 1);
    assert.match(h.messages[0].message, /Cost: 7 credits/);
});

test('a failed correction replaces stale generating progress and unlocks editing', async () => {
    const h = harness(), normal = h.c.respond;
    h.c.setBusy(true); h.c.state.busy = true;
    h.c.progress({ status: 'dubbing', percent: 75, message: 'Generating selected lines' });
    h.c.respond = async url => url.endsWith('/corrections')
        ? { busy: false, history: [{ id: 'pass-1', status: 'failed', percent: 75, message: 'Export failed' }] }
        : normal(url);
    await h.c.poll();
    assert.match(h.get('progress').textContent, /Export failed/);
    assert.doesNotMatch(h.get('progress').textContent, /Generating selected lines/);
    assert.equal(h.get('dub').disabled, false); assert.equal(h.control.disabled, false);
    assert.equal(h.timers.size, 0);
    assert.equal(h.messages.length, 0, 'Failure must not announce a successful export');
});

test('Enter man. opens the one shared time dialog with this line and this project', async () => {
    const h = harness();
    h.c.state.segments = [{ segment_id: 'a' }, { segment_id: 'line-1', start: 3, end: 8, text: 'Hi', arabic_text: 'مرحبا', speaker_id: 's1' }];
    h.c.openPlayer(h.c.state.segments[1]);
    assert.equal(h.c.shared.length, 1);
    const o = h.c.shared[0];
    assert.equal(o.index, 2); assert.equal(o.speaker, 'Sam'); assert.equal(o.start, 3); assert.equal(o.end, 8);
    assert.equal(o.size, 1000); assert.equal(o.kind, 'video'); assert.equal(o.duration, 60);
    assert.equal(o.local, null, 'No file chosen yet, so the server copy is used');
    const src = await o.fetchSource();
    assert.deepEqual(JSON.parse(JSON.stringify(src)), { url: '/api/longdub/parent/media', kind: 'audio' });
    h.c.respond = async () => { throw new Error('nothing to play'); };
    assert.equal(await o.fetchSource(), null, 'No server copy: the dialog asks for the person\'s file');
});

test('a file chosen inside the dialog is remembered for this project only', () => {
    const h = harness();
    h.get('listenFile').files = [];
    h.c.state.segments = [{ segment_id: 'line-1', start: 3, end: 8 }];
    h.c.openPlayer(h.c.state.segments[0]);
    h.c.shared[0].onLocal({ name: 'film.mp4' }, 'blob:mine');
    assert.deepEqual(JSON.parse(JSON.stringify(h.c.listen)), { projectId: 'parent', url: 'blob:mine', name: 'film.mp4' });
    h.c.openPlayer(h.c.state.segments[0]);
    assert.deepEqual(JSON.parse(JSON.stringify(h.c.shared[1].local)), { url: 'blob:mine', kind: 'video' });
    h.c.project = { id: 'other' };
    h.c.openPlayer(h.c.state.segments[0]);
    assert.equal(h.c.shared[2].local, null, 'Another project never reuses it');
});

test('new times are checked, applied to the line and saved', async () => {
    const h = harness(), row = { segment_id: 'line-1', start: 3, end: 8 };
    let saved = 0, drawn = 0;
    h.c.state.segments = [row]; h.c.save = async () => { saved++; }; h.c.renderLines = () => { drawn++; };
    h.c.change = (r, f, v) => { r[f] = v; };
    h.c.openPlayer(row);
    const apply = h.c.shared[0].onApply;
    await assert.rejects(() => apply(9, 9), /valid times/);
    await assert.rejects(() => apply(1, 61), /valid times/);
    assert.equal(saved, 0); assert.equal(row.start, 3);
    assert.equal(await apply(4.5, 9.25), true);
    assert.equal(row.start, 4.5); assert.equal(row.end, 9.25); assert.equal(row.manual_time, true);
    assert.equal(saved, 1); assert.equal(drawn, 1);
});

test('Open needs a film, refuses a file of another size and then opens the project', async () => {
    const h = harness(), opened = () => h.calls.filter(call => call.url === '/api/longdub/parent/corrections').length;
    const select = h.get('projects'), input = h.get('listenFile'), press = async () => { h.get('openProject').onclick(); await new Promise(setImmediate); };
    select.value = ''; input.files = [];
    await press();
    assert.match(h.get('openNote').textContent, /Choose a project first/);
    assert.equal(opened(), 0);
    select.value = 'parent'; input.files = [{ name: 'other.mp4', size: 999 }];
    await press();
    assert.match(h.get('openNote').textContent, /isn’t the same file/);
    assert.equal(opened(), 0);
    input.files = [{ name: 'film.mp4', size: 1000 }];
    await press();
    assert.equal(h.get('openNote').textContent, '');
    assert.equal(opened(), 1);
    assert.equal(h.c.listen.url, 'blob:local-test'); assert.equal(h.c.listen.name, 'film.mp4');
});

test('Open works without a file when our servers still have a copy, and drops another project\'s file', async () => {
    const h = harness(), opened = () => h.calls.filter(call => call.url === '/api/longdub/parent/corrections').length;
    h.get('projects').value = 'parent'; h.get('listenFile').files = [];
    h.c.listen = { projectId: 'someone-else', url: 'blob:old', name: 'old.mp4' };
    h.get('openProject').onclick(); await new Promise(setImmediate);
    assert.equal(opened(), 1);
    assert.equal(h.c.listen, null);
});
