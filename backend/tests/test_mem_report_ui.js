// Built-in Node tests only. The admin memory report shows what is loaded right now.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(path.resolve(__dirname, '..'), 'admin.html'), 'utf8');

function report(data) {
    const start = html.indexOf('async function loadMemDiag()');
    const end = html.indexOf('// ======================= BUSINESS TAB', start);
    assert.ok(start > 0 && end > start);
    const out = { textContent: '' };
    const c = vm.createContext({ document: { getElementById: () => out }, api: async () => ({ ok: true, data }) });
    vm.runInContext(html.slice(start, end), c);
    return c.loadMemDiag().then(() => out.textContent);
}

const base = { cgroup: { version: 'v2', used_mb: 1945.5, limit_mb: 22888.2, cache_mb: 5.9, anon_mb: 1878.8 }, host_meminfo: {}, processes: [] };

test('the report lists what is loaded, counts held in memory and the server process figures', async () => {
    const text = await report(Object.assign({}, base, { runtime: {
        models: { transcription_loaded: false, speaker_detection_loaded: true, activity_detection_loaded: null, processing_jobs: 0, waiting_jobs: 0 },
        threads: { python_live: 12, os_live: 40 },
        process_rollup_mib: { rss_mib: 2086.9, anonymous_mib: 1800.1 },
        container_entries: { long_projects: 3, job_owners: 0, sessions: 7, usage_jobs: null },
        python_objects: { gc_tracked_count: 600000, top_types_by_count: [{ type: 'dict', count: 90000 }] } } }));
    assert.match(text, /Speech model loaded: false \| speaker detection loaded: true \| activity detection loaded: \?/);
    assert.match(text, /Threads: 12 \(system: 40\)/);
    assert.match(text, /rss 2086\.9, anonymous 1800\.1/);
    assert.match(text, /long_projects 3, sessions 7/);
    assert.doesNotMatch(text, /job_owners/);
    assert.match(text, /Python objects: 600000 \(most: dict 90000\)/);
});

test('the old report still prints when the runtime section is missing or unavailable', async () => {
    for (const runtime of [undefined, { error: 'unavailable' }]) {
        const text = await report(Object.assign({}, base, { runtime }));
        assert.match(text, /real app memory \(anon\):  1878\.8 MB/);
        assert.doesNotMatch(text, /What is loaded right now/);
    }
});
