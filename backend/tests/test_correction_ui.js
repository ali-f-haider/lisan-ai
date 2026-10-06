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
    const c = vm.createContext({
        lang: 'en', project: { id: 'parent' }, state: { busy: false, history: [], segments: [], duration: 60 },
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
    vm.runInContext(excerpt('  async function openPlayer(', "  $('projects').onchange="), c);
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

test('audio-only manual timing can pause and resume through its explicit playback button', async () => {
    const h = harness();
    await h.c.openPlayer({ segment_id: 'line-1', start: 3, end: 8 });
    const media = h.created.find(node => node.tagName === 'video');
    const play = h.created.find(node => node.tagName === 'button' && node.textContent === 'Play');
    const playRange = h.created.find(node => node.tagName === 'button' && node.textContent === 'Play this range');
    assert.ok(media.classList.contains('audio'), 'This exercises the hidden native audio controls');
    assert.ok(play, 'An explicit playback control remains available');
    playRange.click();
    assert.equal(media.currentTime, 3); assert.equal(media.paused, false); assert.equal(play.textContent, 'Pause');
    play.click(); assert.equal(media.paused, true); assert.equal(play.textContent, 'Play');
    play.click(); assert.equal(media.paused, false); assert.equal(play.textContent, 'Pause');
});
