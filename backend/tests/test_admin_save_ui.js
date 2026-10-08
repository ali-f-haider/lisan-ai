// A refused pricing save (HTTP 200 with ok:false) must never show the green "saved" message.
const fs = require('fs'), assert = require('assert'), path = require('path');
const html = fs.readFileSync(path.join(__dirname, '..', 'admin.html'), 'utf8');
const start = html.indexOf("api('/api/admin/pricing', {");
assert(start > 0, 'save call not found');
const block = html.slice(start, start + 900);
assert(/res\.ok\s*&&\s*res\.data\s*&&\s*res\.data\.ok\s*!==\s*false/.test(block), 'save must check the server answer, not only the HTTP status');
assert(/toast\('Failed: '/.test(block), 'a refused save must show the real reason');
console.log('admin save ui ok');
