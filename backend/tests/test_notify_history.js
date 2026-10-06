// Built-in Node tests only: the notification history list (this session only).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'styles.css'), 'utf8');
const assistant = fs.readFileSync(path.join(root, 'assistant_service.py'), 'utf8');

function excerpt(source, first, last) {
    const start = source.lastIndexOf(first);
    assert.ok(start >= 0, first);
    const end = source.indexOf(last, start + first.length);
    assert.ok(end > start, last);
    return source.slice(start, end);
}

function context(stored) {
    const store = Object.assign({}, stored);
    const c = vm.createContext({
        sessionStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } },
        document: { getElementById() { return null; } },
        subsText: (en) => en, JSON, Date, isFinite, Array
    });
    vm.runInContext(excerpt(app, 'const NOTIFY_LOG_KEY', 'function placeNotifyHistoryUI'), c);
    return { c, store };
}

test('every notification is written to the history with its time, type and text', () => {
    const { c } = context();
    c.recordNotification('error', 'Something went wrong');
    c.recordNotification('weird', 'Hello');
    c.recordNotification('success', '   ');       // empty messages are ignored
    assert.equal(c.notifyLog.length, 2);
    assert.equal(c.notifyLog[0].type, 'error');
    assert.equal(c.notifyLog[1].type, 'info');
    assert.ok(c.notifyLog[0].t > 0);
    assert.equal(c.notifyUnread, 2);
});

test('the history keeps at most 200 messages and lives only in the tab (sessionStorage)', () => {
    const { c, store } = context();
    for (let i = 0; i < 230; i++) c.recordNotification('info', 'm' + i);
    assert.equal(c.notifyLog.length, 200);
    assert.equal(c.notifyLog[0].text, 'm30');
    assert.equal(JSON.parse(store.lisan_notify_log).length, 200);
});

test('a reloaded page gets its list back; another account does not see it; clearing empties it', () => {
    const first = context();
    first.c.notifyLogForUser('Ali');
    first.c.recordNotification('info', 'kept');
    const again = context(first.store);
    assert.equal(again.c.notifyLog.length, 1);
    again.c.notifyLogForUser('Ali');
    assert.equal(again.c.notifyLog.length, 1);
    again.c.notifyLogForUser('Someone else');
    assert.equal(again.c.notifyLog.length, 0);
    assert.equal(JSON.parse(again.store.lisan_notify_log).length, 0);
});

test('a damaged saved list is ignored', () => {
    const { c } = context({ lisan_notify_log: '{not json' });
    assert.equal(c.notifyLog.length, 0);
    const bad = context({ lisan_notify_log: JSON.stringify([{ foo: 1 }, { t: 5, type: 'info', text: 'ok' }]) });
    assert.equal(bad.c.notifyLog.length, 1);
});

test('log out clears the history and nothing is sent to the server', () => {
    assert.ok(/function doLogout\(\) \{\s*try \{ sessionStorage\.removeItem\(NOTIFY_LOG_KEY\)/.test(app));
    const body = excerpt(app, 'const NOTIFY_LOG_KEY', 'function buildNotifyHistoryUI');
    assert.ok(!/fetch\(|XMLHttpRequest|sendBeacon/.test(body));
});

test('the V tab and the list are styled, and the pop-ups sit below the tab', () => {
    assert.ok(css.includes('#notifyHistoryBtn') && css.includes('#notifyHistoryPanel'));
    assert.ok(/#notifyPanel \{[^}]*top: calc\(var\(--ubh, 56px\) \+ 30px\)/.test(css));
});

test('the AI helper knows about the notification history', () => {
    assert.ok(assistant.includes('NOTIFICATION HISTORY'));
    assert.ok(/V-shaped arrow/.test(assistant) && /NOT saved on our servers/.test(assistant));
});
