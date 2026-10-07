// The page's credit notes are not out of date, and a zero price on Auto-Assign explains itself.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

test('the old claim that a full dub costs only a few credits is gone, in the page and in both languages', () => {
    for (const text of [app, html]) {
        assert.ok(!/costs only a few credits/.test(text));
        assert.ok(!/بضعة ائتمانات فقط/.test(text));
    }
});

test('the credits note says every button shows its price and that text steps join the Generate price', () => {
    assert.match(html, /Every button shows its price before you click it/);
    assert.match(html, /added to the price of <strong>Generate Arabic Audio<\/strong>/);
    assert.match(app, /كل زر يعرض سعره قبل أن تضغط عليه/);
});

test('the temporary-file warning is still shown (it matches the real retention)', () => {
    assert.match(html, /id="tempFileWarning"/);
});

test('a zero Auto-Assign price says why on hover', () => {
    assert.match(app, /Free now: every speaker already has a voice/);
    assert.match(app, /مجاني الآن/);
});
