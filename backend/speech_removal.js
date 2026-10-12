/* Explicit source-speech selection; output line times are never changed here. */
(function () {
  'use strict';
  var records = new Map(), imported = new Map(), opening = false;
  function copy(ranges) {
    if (!Array.isArray(ranges) || ranges.length > 256) throw new Error('Invalid speech sections.');
    return ranges.map(function (p) {
      if (!Array.isArray(p) || p.length !== 2 || !p.every(function (x) { return typeof x === 'number' && Number.isFinite(x); }) || p[0] < 0 || p[1] <= p[0]) throw new Error('Invalid speech section.');
      return p.slice();
    });
  }
  async function request(url, method, body) {
    var opts = {method: method, credentials: 'same-origin'};
    if (body) { opts.headers = {'Content-Type': 'application/json'}; opts.body = JSON.stringify(body); }
    var response = await fetch(url, opts), result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not save the speech section. Try again.');
    if (!result || typeof result.revision !== 'string') throw new Error('Could not verify the saved speech sections. Try again.');
    result.ranges = copy(result.ranges);
    return result;
  }
  async function load(url, job) {
    var result = await request(url, 'GET');
    if (imported.has(job)) {
      result = await request(url, 'PUT', {ranges: copy(imported.get(job)), revision: result.revision});
      imported.delete(job);
    }
    records.set(job, result);
    return result;
  }
  async function open(opts) {
    if (opening || !window.LisanPlayer || LisanPlayer.isOpen() || (opts.busy && opts.busy())) return;
    var record;
    opening = true;
    try { record = await load(opts.endpoint, opts.job); }
    catch (ex) { if (opts.error) opts.error(ex); return; }
    finally { opening = false; }
    if ((opts.current && !opts.current()) || (opts.busy && opts.busy()) || LisanPlayer.isOpen()) return;
    async function save(values) {
      var next;
      try { next = await request(opts.endpoint, 'PUT', {ranges: values, revision: record.revision}); }
      catch (ex) { if (opts.player && opts.player.lang === 'ar') throw new Error('تعذّر حفظ المقطع. أعد فتح المشغل، وراجع التوقيت ثم حاول مرة أخرى.'); throw ex; }
      record = next; records.set(opts.job, record);
      if (opts.saved && (!opts.current || opts.current())) opts.saved(next);
      return copy(next.ranges);
    }
    var player = Object.assign({}, opts.player, {
      purpose: 'speech-removal', savedRanges: copy(record.ranges),
      onApply: async function (a, b) {
        if (opts.current && !opts.current()) return false;
        await save(record.ranges.concat([[a, b]]));
        return true;
      },
      onUndo: async function (index) {
        if (opts.current && !opts.current()) throw new Error('Reopen the current project.');
        var values = copy(record.ranges); values.splice(index, 1);
        return save(values);
      }
    });
    LisanPlayer.open(player);
  }
  function button(parent, callback, lang, className) {
    var b = document.createElement('button'); b.type = 'button'; b.className = className || 'btn-plain';
    b.textContent = lang === 'ar' ? 'إزالة الكلام' : 'Remove speech';
    b.title = lang === 'ar' ? 'حدد مقطعًا لا يزال فيه الكلام الأصلي مسموعًا.' : 'Select a section where original speech is still audible.';
    b.onclick = callback; parent.appendChild(b); return b;
  }
  window.LisanSpeechRemoval = {
    open: open, button: button, copy: copy,
    sync: load,
    remember: function (job, ranges) { imported.set(job, copy(ranges)); records.set(job, {ranges: copy(ranges)}); },
    projectData: function (job) { return records.has(job) ? copy(records.get(job).ranges) : undefined; },
    saved: function (job) { return copy((records.get(job) || {}).ranges || []); }
  };
})();
