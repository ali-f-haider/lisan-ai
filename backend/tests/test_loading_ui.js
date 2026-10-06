// Built-in Node tests only: the loading screen at start-up, the loading panel while translating, the speaker column width.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const index = fs.readFileSync(path.join(root, 'index.html'), 'utf8');

function excerpt(source, first, last) {
    const start = source.lastIndexOf(first);
    assert.ok(start >= 0, first);
    const end = source.indexOf(last, start + first.length);
    assert.ok(end > start, last);
    return source.slice(start, end);
}

function context(fetchImpl) {
    const shown = [];
    const els = new Map();
    const make = () => ({ style: {}, children: [], setAttribute() {}, appendChild(c) { this.children.push(c); }, textContent: '' });
    const doc = {
        getElementById(id) { return els.get(id) || null; },
        createElement() { const e = make(); return e; },
        body: { appendChild(e) { els.set(e.id, e); if (e.children[1]) { /* card */ } } }
    };
    // the overlay builds a card with children, so register nested ids too
    const origCreate = doc.createElement;
    doc.createElement = function () { const e = origCreate(); Object.defineProperty(e, 'id', { set(v) { this._id = v; els.set(v, this); }, get() { return this._id; } }); return e; };
    const messages = [];
    const c = vm.createContext({
        window: { currentLang: 'en' }, document: doc, messages,
        segmentsData: [{ segment_id: 's1', text: 'Hi', locked: false }], currentJobId: 'a'.repeat(32),
        notify(type, text) { messages.push({ type, text }); }, fetchUsage() {}, renderTable() {}, sanitizeStyle: v => v || '',
        fetch: fetchImpl
    });
    vm.runInContext('function subsText(en, ar) { return window.currentLang === "ar" ? ar : en; }', c);
    vm.runInContext(excerpt(app, 'var _busyCount', 'const SUBS_REASON_AR'), c);
    vm.runInContext(excerpt(app, 'async function autoTranslate()', 'async function detectEmotions()'), c);
    return { c, els, messages };
}

test('Auto Translate shows the loading panel while it waits and hides it afterwards', async () => {
    let during = null, ctx;
    ctx = context(async () => { during = ctx.els.get('busyOverlay').style.display; return { json: async () => ({ translated_segments: [{ segment_id: 's1', arabic_text: 'مرحبا' }] }) }; });
    await ctx.c.autoTranslate();
    assert.equal(during, 'flex');
    assert.equal(ctx.els.get('busyOverlay').style.display, 'none');
    assert.ok(ctx.els.get('busyOverlayText').textContent.includes('Translating'));
});

test('the loading panel is also hidden when translation fails, and shows Arabic text in Arabic', async () => {
    const ctx = context(async () => { throw new Error('network'); });
    ctx.c.window.currentLang = 'ar';
    await ctx.c.autoTranslate();
    assert.equal(ctx.els.get('busyOverlay').style.display, 'none');
    assert.ok(/[؀-ۿ]/.test(ctx.els.get('busyOverlayText').textContent));
});

test('the app stays behind a loading screen until the account is known', () => {
    assert.ok(index.includes('id="appGate"'));
    assert.ok(index.indexOf('id="appGate"') < index.indexOf('id="userBar"'));
    assert.ok(index.includes('window.hideAppGate'));
    // both the success and the failure path of loading the account open the app
    const blocks = app.split('(function loadUserInfo()').slice(1);
    assert.ok(blocks.length >= 1);
    blocks.forEach(b => { const body = b.slice(0, b.indexOf('})();')); assert.equal((body.match(/window\.hideAppGate\(\)/g) || []).length, 2); });
});

test('the speaker column in Step 2 is 1 cm (38 px) wider', () => {
    assert.ok(/<th style="width:138px" id="thSpeaker">/.test(index));
});
