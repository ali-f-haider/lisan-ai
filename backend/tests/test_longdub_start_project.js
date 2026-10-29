// The long-dub page opens the project open last time, or one still on its way; never a closed project in review by itself.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '..', 'dub_long.html'), 'utf8');

function cut(first, last) {
    const a = html.indexOf(first), b = html.indexOf(last, a + first.length);
    assert.ok(a >= 0 && b > a, first);
    return html.slice(a, b);
}

function pick(jobs, stored) {
    const store = new Map(stored ? [['lisan_longdub_job', stored]] : []);
    const ctx = vm.createContext({ jobs, localStorage: { getItem: k => store.has(k) ? store.get(k) : null } });
    vm.runInContext('var LS_KEY = "lisan_longdub_job";\n' + cut('function isOpen(st)', 'function arSpeakers') + cut('function pickInitial()', 'function selectJob'), ctx);
    const j = vm.runInContext('pickInitial()', ctx);
    return j ? j.id : null;
}

const JOBS = [{ id: 'new_done', status: 'done' }, { id: 'thinner', status: 'editing' }, { id: 'old_editing', status: 'editing' }];

test('a closed project in review does not open by itself', () => {
    assert.equal(pick(JOBS, null), null);
});

test('the project open last time opens again, also when it is in review', () => {
    assert.equal(pick(JOBS, 'thinner'), 'thinner');
});

test('a project still on its way opens by itself', () => {
    assert.equal(pick([{ id: 'a', status: 'done' }, { id: 'b', status: 'analyzing' }, { id: 'c', status: 'editing' }], null), 'b');
    assert.equal(pick([{ id: 'e', status: 'estimated' }], null), 'e');
});

test('a remembered project that no longer exists falls back the same way', () => {
    assert.equal(pick(JOBS, 'deleted'), null);
});
