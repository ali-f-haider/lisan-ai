// The Step 4 voice list keeps the server's order (Arabic voices first) and warns about voices not made for Arabic.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
function load(lang = 'en') {
    const c = vm.createContext({window: {currentLang: lang}, voicePools: {male: [], female: []}});
    vm.runInContext('function subsText(en, ar) { return window.currentLang === "ar" ? ar : en; }', c);
    const start = app.indexOf('// The voice list may carry its own `order`');
    const end = app.indexOf('async function ensureVoicePools()', start);
    assert.ok(start >= 0 && end > start);
    vm.runInContext(app.slice(start, end), c);
    return c;
}

test('without an order the numbering still follows the voice id, as before', () => {
    const c = load();
    c.buildVoicePools([{voice_id: 'b', gender: 'male'}, {voice_id: 'a', gender: 'male'}, {voice_id: 'c', gender: 'female'}]);
    assert.deepEqual(c.voicePools.male.map(v => v.voice_id), ['a', 'b']);
    assert.deepEqual(c.voicePools.female.map(v => v.voice_id), ['c']);
});

test('with an order from the server, that order wins (the Arabic voices stay first)', () => {
    const c = load();
    c.buildVoicePools([{voice_id: 'Alex', gender: 'male', order: 2}, {voice_id: 'Omar', gender: 'male', order: 0}, {voice_id: 'Zed', gender: 'male', order: 1}]);
    assert.deepEqual(c.voicePools.male.map(v => v.voice_id), ['Omar', 'Zed', 'Alex']);
});

test('a voice not made for Arabic carries a short warning, in both languages; others do not', () => {
    const en = load('en'), ar = load('ar');
    assert.equal(en.voiceOptionText('🎲 Male voice', {arabic: false}, 2), '🎲 Male voice 3 (not made for Arabic)');
    assert.equal(en.voiceOptionText('🎲 Male voice', {arabic: true}, 0), '🎲 Male voice 1');
    assert.equal(en.voiceOptionText('🎲 Male voice', {}, 0), '🎲 Male voice 1');               // the other engine's list has no such field
    assert.match(ar.voiceOptionText('🎲 Male voice', {arabic: false}, 0), /غير مخصص للعربية/);
});

test('all three Step 4 dropdowns and the browser use the warning text', () => {
    assert.equal((app.match(/o\.textContent = voiceOptionText\(label, p, i\)/g) || []).length, 3);
    assert.match(app, /label: voiceOptionText\("🎲 Male voice", v, i\)/);
    assert.match(app, /label: voiceOptionText\("🎲 Female voice", v, i\)/);
});

// ---- permanent numbers: "Male voice 17" is a number given by the server, not a position in the list
function numberedContext(pools) {
    const c = vm.createContext({window: {currentLang: 'en'}, voicePools: pools, speakerChoices: {}, speakerVoices: {}, speakerVoiceNames: {}, clonedBySpeaker: {}, Math, Object, Set, parseInt});
    vm.runInContext('function subsText(en, ar) { return window.currentLang === "ar" ? ar : en; }', c);
    const start = app.indexOf('// The voice list may carry its own `order`');
    vm.runInContext(app.slice(start, app.indexOf('async function ensureVoicePools()', start)), c);
    const a = app.indexOf('function applyChoice(name)');
    vm.runInContext(app.slice(a, app.indexOf('async function renderSpeakerVoices()', a)), c);
    return c;
}
const gappy = () => ({male: [{voice_id: 'A', number: 1}, {voice_id: 'C', number: 3}, {voice_id: 'D', number: 7}], female: []});

test('a choice is found by the voice number, even when numbers have gaps', () => {
    const c = numberedContext(gappy());
    c.speakerChoices.Ann = 'male:7';
    c.applyChoice('Ann');
    assert.equal(c.speakerVoices.Ann, 'D');
    assert.equal(c.speakerChoices.Ann, 'male:7');                       // stays the same number
    assert.equal(c.speakerVoiceNames.Ann, '🎲 Male voice 7');
});

test('a voice that is gone is replaced by the closest number, and the choice then names the real voice', () => {
    const c = numberedContext(gappy());
    c.speakerChoices.Ann = 'male:5';                                    // number 5 was retired; 3 and 7 are both two away, 3 comes first
    c.applyChoice('Ann');
    assert.equal(c.speakerVoices.Ann, 'C');
    assert.equal(c.speakerChoices.Ann, 'male:3');
});

test('without server numbers the old position numbering still works', () => {
    const c = numberedContext({male: [{voice_id: 'A'}, {voice_id: 'B'}], female: []});
    c.speakerChoices.Ann = 'male:2';
    c.applyChoice('Ann');
    assert.equal(c.speakerVoices.Ann, 'B');
    assert.equal(c.voiceNumber(c.voicePools.male[1], 1), 2);
});

test('the option text and the browser use the number, not the position', () => {
    const c = numberedContext(gappy());
    assert.equal(c.voiceOptionText('🎲 Male voice', c.voicePools.male[2], 2), '🎲 Male voice 7');
    assert.equal(c.poolIndexOfNumber(c.voicePools.male, 3), 1);
    assert.equal(c.poolIndexOfNumber(c.voicePools.male, 2), -1);
});

test('every place that writes a choice uses the voice number', () => {
    assert.equal((app.match(/g \+ ":" \+ voiceNumber\(pool\[pick\], pick\)/g) || []).length, 5);
    assert.match(app, /found = g \+ ":" \+ voiceNumber\(voicePools\[g\]\[i\], i\)/);
    assert.equal((app.match(/o\.value = g \+ ":" \+ voiceNumber\(p, i\)/g) || []).length, 3);
    assert.ok(!/g \+ ":" \+ \(\w+ \+ 1\)/.test(app), 'no choice is built from a position any more');
});
