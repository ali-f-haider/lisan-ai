// Built-in Node tests: Auto-Assign asks the server for matching voices and never loses the old behaviour.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
function excerpt(source, first, last) {
    const start = source.lastIndexOf(first); assert.ok(start >= 0, first);
    const end = source.indexOf(last, start + first.length); assert.ok(end > start, last);
    return source.slice(start, end);
}
function element(tag) {
    return {tag, style: {}, children: [], options: [], appendChild(child) { this.children.push(child); if (child.tag === 'option') this.options.push(child); }};
}
function context(opts = {}) {
    const messages = [], fetches = [];
    const c = vm.createContext({
        window: {currentLang: 'en'}, document: {createElement: element}, isFinite, Object, Math, JSON,
        subsText: en => en, notify: (type, message) => messages.push({type, message}),
        currentJobId: 'a'.repeat(32), clonedBySpeaker: {}, speakerChoices: {}, speakerVoices: {}, speakerVoiceNames: {},
        voicePools: {male: [{voice_id: 'm1'}, {voice_id: 'm2'}], female: [{voice_id: 'f1'}]},
        segmentsData: [
            {speaker: 'Grandpa', start: 0, end: 4, gender: 'male'}, {speaker: 'Lily', start: 5, end: 9, gender: 'male'},
            {speaker: 'Lily', start: 10, end: 12, gender: 'male'}, {speaker: 'Zed', start: 13, end: 14, gender: 'male'}],
        ensureVoicePools: async () => true, applyChoice(name) { const [g, n] = String(c.speakerChoices[name]).split(':'); c.speakerVoices[name] = (c.voicePools[g] || [])[Number(n) - 1]?.voice_id; },
        renderSpeakerVoices() {}, scheduleGeneratePrice() {},
        async fetch(url, options) {
            fetches.push({url, body: JSON.parse(options.body)});
            if (opts.fail === 'throw') throw new Error('offline');
            if (opts.fail === 'status') return {ok: false, json: async () => ({})};
            return {ok: true, json: async () => opts.reply};
        }
    });
    vm.runInContext(excerpt(app, 'var speakerGenderPick = {};', 'function updateSpeakerName('), c);
    vm.runInContext(excerpt(app, 'async function autoAssignVoices()', '// ===== VOICE LIBRARY BROWSER'), c);
    return {c, messages, fetches};
}
const reply = speakers => ({speakers, listener: 'used'});

test('each speaker gets the voice the server picked, mapped to the right list position', async () => {
    const {c, fetches} = context({reply: reply({
        Grandpa: {gender: 'male', age: 'senior', best: 'm2', fit: 'good'},
        Lily: {gender: 'female', age: 'young', best: 'f1', fit: 'good'},
        Zed: {gender: 'male', age: 'adult', best: 'm1', fit: 'good'}})});
    await c.autoAssignVoices();
    assert.deepEqual({...c.speakerChoices}, {Grandpa: 'male:2', Lily: 'female:1', Zed: 'male:1'});
    assert.equal(fetches[0].url, '/api/voices/match');
    assert.equal(fetches[0].body.job_id, 'a'.repeat(32));
    assert.deepEqual(fetches[0].body.segments.filter(s => s.speaker === 'Lily').length, 2);
});

test('detected gender fills the radios, but a gender the person chose is never overridden and is sent to the server', async () => {
    const {c, fetches} = context({reply: reply({
        Grandpa: {gender: 'female', best: 'f1', fit: 'good'}, Lily: {gender: 'female', best: 'f1', fit: 'good'}, Zed: {gender: 'male', best: 'm1', fit: 'good'}})});
    c.speakerGenderByName.Grandpa = 'male';              // chosen by the person
    await c.autoAssignVoices();
    const sent = Object.fromEntries(fetches[0].body.speakers.map(s => [s.name, s.gender]));
    assert.deepEqual(sent, {Grandpa: 'male', Lily: '', Zed: ''});          // only a real choice is sent
    assert.equal(c.speakerGenderByName.Grandpa, 'male');                   // kept
    assert.equal(c.speakerGenderByName.Lily, 'female');                    // detected
    assert.equal(c.speakerGenderAuto.Lily, true);
    assert.ok(!c.speakerGenderAuto.Grandpa);
    assert.equal(c.segmentsData.filter(s => s.speaker === 'Lily').every(s => s.gender === 'female'), true);
});

test('a detected gender is sent as "not chosen" the next time, so it can be detected again', async () => {
    const {c, fetches} = context({reply: reply({Grandpa: {gender: 'male', best: 'm1', fit: 'good'}, Lily: {gender: 'female', best: 'f1', fit: 'good'}, Zed: {gender: 'male', best: 'm2', fit: 'good'}})});
    await c.autoAssignVoices();
    c.speakerChoices = {}; c.speakerVoices = {};
    await c.autoAssignVoices();
    assert.deepEqual(fetches[1].body.speakers.map(s => s.gender), ['', '', '']);
});

test('the age the person picked is sent, and speakers that already have a voice are kept and reserved', async () => {
    const {c, fetches} = context({reply: reply({Lily: {gender: 'female', best: 'f1', fit: 'good'}, Zed: {gender: 'male', best: 'm1', fit: 'good'}})});
    c.speakerAgeByName.Zed = 'senior';
    c.speakerChoices.Grandpa = 'male:2'; c.speakerVoices.Grandpa = 'm2';
    await c.autoAssignVoices();
    assert.deepEqual(fetches[0].body.speakers.map(s => s.name), ['Lily', 'Zed']);
    assert.equal(fetches[0].body.speakers[1].age, 'senior');
    assert.deepEqual(fetches[0].body.taken, ['m2']);
    assert.equal(c.speakerChoices.Grandpa, 'male:2');
});

for (const fail of ['throw', 'status']) {
    test(`if matching is unavailable (${fail}) every speaker still gets a voice the old way`, async () => {
        const {c, messages} = context({fail});
        await c.autoAssignVoices();
        assert.deepEqual(Object.keys(c.speakerChoices).sort(), ['Grandpa', 'Lily', 'Zed']);
        assert.ok(Object.values(c.speakerChoices).every(v => /^(male|female):\d+$/.test(v)));
        assert.ok(messages.some(m => m.type === 'success'));
    });
}

test('an answer naming a voice that is not in the list falls back to a normal pick for that speaker', async () => {
    const {c} = context({reply: reply({Grandpa: {gender: 'male', best: 'gone', fit: 'good'}, Lily: {gender: 'female', best: 'f1', fit: 'good'}, Zed: {best: null, fit: 'none'}})});
    await c.autoAssignVoices();
    assert.deepEqual(Object.keys(c.speakerChoices).sort(), ['Grandpa', 'Lily', 'Zed']);
    assert.equal(c.speakerChoices.Lily, 'female:1');
});

test('a rough fit is explained and suggests cloning; the hint says what was heard', async () => {
    const {c, messages} = context({reply: reply({Grandpa: {gender: 'male', age: 'senior', best: 'm1', fit: 'rough'}, Lily: {gender: 'female', age: 'young', best: 'f1', fit: 'good'}, Zed: {gender: 'male', best: 'm2', fit: 'good'}})});
    await c.autoAssignVoices();
    assert.ok(messages.some(m => m.type === 'info' && /1 speaker\(s\).*Cloning/.test(m.message)));
    assert.match(c.speakerMatchNote.Grandpa, /older male/);
    assert.match(c.speakerMatchNote.Grandpa, /Cloning this speaker/);
    assert.match(c.speakerMatchNote.Lily, /young female/);
});

test('changing the age of an automatically picked voice picks again; a hand-picked voice is left alone', async () => {
    const {c, fetches} = context({reply: reply({Grandpa: {gender: 'male', best: 'm1', fit: 'good'}, Lily: {gender: 'female', best: 'f1', fit: 'good'}, Zed: {gender: 'male', best: 'm2', fit: 'good'}})});
    await c.autoAssignVoices();
    const before = fetches.length;
    const select = c.ageSelectFor('Grandpa');
    select.value = 'senior'; select.onchange();
    await new Promise(r => setTimeout(r, 0));
    assert.equal(c.speakerAgeByName.Grandpa, 'senior');
    assert.equal(fetches.length, before + 1);
    assert.equal(fetches[before].body.speakers[0].age, 'senior');
    delete c.speakerChoiceAuto.Lily;                       // the person picked Lily's voice by hand
    const s2 = c.ageSelectFor('Lily'); s2.value = 'young'; s2.onchange();
    await new Promise(r => setTimeout(r, 0));
    assert.equal(fetches.length, before + 1);              // no new request, the choice stays
    assert.equal(c.speakerChoices.Lily, 'female:1');
    s2.value = ''; s2.onchange();
    assert.ok(!('Lily' in c.speakerAgeByName));
});

test('Step 1.5 starts with no gender picked, so a default cannot pass as a choice', () => {
    const {c} = context({reply: reply({})});
    const wrap = c.genderRadios('Anna', 'g', () => {}, '');
    const radios = wrap.children.map(l => l.children[0]);
    assert.equal(radios.length, 2);
    assert.ok(radios.every(r => r.checked === false));
    const other = c.genderRadios('Anna', 'g', () => {});     // Step 4 keeps showing the usual default
    assert.ok(other.children.map(l => l.children[0]).some(r => r.checked));
});

test('the age list offers automatic, child, young, adult and older', () => {
    const {c} = context({reply: reply({})});
    assert.deepEqual(c.ageSelectFor('x').options.map(o => o.value), ['', 'child', 'young', 'adult', 'senior']);
});
