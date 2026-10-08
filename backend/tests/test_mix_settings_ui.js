// Built-in Node tests only; no website, AI, database or payment calls.
// Re-speaking or re-stretching ONE line rebuilds the whole final audio, so it must carry the
// "use silent gap", "no overlap" and dragged-offset choices the user already made.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = fs.readFileSync(path.join(path.resolve(__dirname, '..'), 'app.js'), 'utf8');

function excerpt(first, last) {
    const start = app.lastIndexOf(first);
    assert.ok(start >= 0, first);
    const end = app.indexOf(last, start + first.length);
    assert.ok(end > start, last);
    return app.slice(start, end);
}

function context() {
    const elements = new Map(), calls = [];
    const store = new Map();
    const c = vm.createContext({
        window: { currentLang: 'en', crypto: require('node:crypto').webcrypto,
                  overlapAllowed: { seg_1: true }, deadSpaceAllowed: { seg_0: true } },
        localStorage: { getItem: k => store.get(k) || null, setItem: (k, v) => store.set(k, v), removeItem: k => store.delete(k) },
        document: { getElementById(id) { if (!elements.has(id)) elements.set(id, { value: 'exact', disabled: false, textContent: '' }); return elements.get(id); }, querySelector() { return null; } },
        segmentsData: [{ segment_id: 'seg_0', arabic_text: 'مرحبا', emotion: 'neutral', speaker: 'Speaker 1', start: 0 }],
        speakerVoices: { 'Speaker 1': 'voice' }, currentJobId: 'a'.repeat(32), totalDuration: 4,
        segmentOffsets: { seg_0: 0.4, seg_1: 0, seg_2: -0.0004 },
        setBadge() {}, subsText: (en) => en, fetchUsage() {}, refreshCredits() {}, notify() {},
        renderTimeline() {}, Date, encodeURIComponent, JSON, Math, Object, Array, Number, String,
        async fetch(url, options) {
            const payload = JSON.parse(options.body);
            calls.push({ url, payload });
            if (url.endsWith('/quote')) return { ok: true, async json() { return { credits: 2, voice_credits: 2, analysis_credits: 0, required_balance: 2 }; } };
            return { ok: true, async json() { return { status: 'success', credits_charged: 2 }; } };
        }
    });
    vm.runInContext(excerpt('function shortWaqfMode(', '\nfunction saveProject('), c);
    vm.runInContext(excerpt('function currentMixSettings(', '// Pure editing action'), c);
    vm.runInContext(excerpt('async function restretchLine(', '\nasync function startTranscribe('), c);
    return { c, calls, store };
}

test('re-speaking one line sends the silent-gap, overlap and offset choices, in the price check and the request', async () => {
    const { c, calls } = context();
    await c.regenerateLine(0, { disabled: false, textContent: '' });
    const sent = calls.filter(call => /regenerate_line/.test(call.url));
    assert.equal(sent.length, 2);
    for (const call of sent) {
        assert.deepEqual(call.payload.dead_space_allowed, { seg_0: true });
        assert.deepEqual(call.payload.overlap_allowed, { seg_1: true });
        assert.deepEqual(call.payload.offsets, { seg_0: 0.4 });
    }
});

test('re-stretching one line sends the same choices', async () => {
    const { c, calls } = context();
    await c.restretchLine(c.segmentsData[0]);
    assert.equal(calls.length, 1);
    assert.match(calls[0].url, /restretch_line/);
    assert.deepEqual(calls[0].payload.dead_space_allowed, { seg_0: true });
    assert.deepEqual(calls[0].payload.overlap_allowed, { seg_1: true });
    assert.deepEqual(calls[0].payload.offsets, { seg_0: 0.4 });
});

test('the choices are read when the click happens, so a later change is picked up', async () => {
    const { c, calls } = context();
    c.window.deadSpaceAllowed = { seg_0: true, seg_5: true };
    c.segmentOffsets = {};
    await c.restretchLine(c.segmentsData[0]);
    assert.deepEqual(calls[0].payload.dead_space_allowed, { seg_0: true, seg_5: true });
    assert.deepEqual(calls[0].payload.offsets, {});
});

test('a changed checkbox does not block retrying an unresolved paid request', async () => {
    const { c, calls, store } = context();
    await c.regenerateLine(0, { disabled: false, textContent: '' });
    const key = [...store.keys()].find(k => k.startsWith('lisan_regenerate_v1:'));
    // Pretend the first click never finished: the saved request stays in storage.
    store.set(key, JSON.stringify(calls.find(call => call.url.endsWith('regenerate_line')).payload));
    c.window.deadSpaceAllowed = {};
    calls.length = 0;
    await c.regenerateLine(0, { disabled: false, textContent: '' });
    const retry = calls.find(call => call.url.endsWith('regenerate_line'));
    assert.ok(retry, 'the retry must still be sent');
    assert.ok(retry.payload.operation_id);
});
