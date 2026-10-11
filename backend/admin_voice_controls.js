/* Owner-only voice storage controls. No cleanup occurs on page load. */
(function (root) {
  'use strict';
  var inventory = null, timer = null, pending = '';
  var labels = { saved: 'Saved library', active: 'Running job', retained: 'Retained for corrections',
    expired: 'Expired / released', orphan: 'Leftover long-dub clone', short_untracked: 'Short-dub / untracked clone', other: 'Other workspace voice', unverified: 'References not verified' };
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function table(data) {
    return '<table><thead><tr><th>Select</th><th>Voice</th><th>Use</th><th>Project / expiry</th></tr></thead><tbody>' + data.voices.map(function(row) {
      var projects = row.projects.map(function(p) { return esc(p.name) + (Number.isFinite(p.expires_at) ? ' · ' + esc(new Date(p.expires_at * 1000).toLocaleString()) : ' · expiry unknown'); }).join('<br>');
      return '<tr><td><input type="checkbox" class="voice-storage-pick" value="' + esc(row.id) + '"' + (row.selectable && !data.cleanup_busy ? '' : ' disabled') + '></td><td>' + esc(row.name || row.id) + '</td><td>' + esc(labels[row.category] || 'Unverified') + '</td><td>' + (projects || (row.saved_rows ? row.saved_rows + ' saved library entry/entries' : 'No recorded long-project reference')) + '</td></tr>';
    }).join('') + '</tbody></table>';
  }
  function el(id) { return document.getElementById(id); }
  async function loadSettings() {
    var r = await api('/api/admin/voice_management');
    if (!r.ok) { toast(r.data.error, true); return; }
    el('voiceRetentionDays').value = r.data.settings.retention_days;
  }
  async function saveSettings() {
    var days = Number(el('voiceRetentionDays').value);
    if (!Number.isInteger(days) || days < 1 || days > 30) { toast('Choose 1 to 30 whole days.', true); return; }
    var r = await api('/api/admin/voice_management/settings', {method:'POST',body:JSON.stringify({retention_days:days})});
    if (!r.ok) { toast(r.data.error, true); return; }
    toast('Voice retention saved. The next cleanup also uses this period for existing projects.');
    if (inventory) await refresh();
  }
  async function refresh() {
    el('voiceInventoryNote').textContent = 'Checking voice storage, projects and saved library…';
    var r = await api('/api/admin/voice_management/inventory');
    if (!r.ok) { inventory = null; el('voiceInventoryRows').innerHTML = ''; el('voiceInventoryNote').textContent = r.data.error; return; }
    inventory = r.data;
    el('voiceInventoryNote').textContent = inventory.total + ' stored voices · ' + Object.keys(labels).map(function(k) { return labels[k] + ': ' + (inventory.counts[k] || 0); }).join(' · ') + (inventory.warning ? ' · ' + inventory.warning : inventory.cleanup_busy ? ' · Cleanup blocked while jobs are running.' : '');
    el('voiceInventoryRows').innerHTML = table(inventory);
    var resume = el('voiceCleanupResume');
    resume.innerHTML = '';
    inventory.pending_cleanup.forEach(function(token) {
      var button = document.createElement('button'); button.className='btn'; button.textContent='Retry interrupted cleanup';
      button.onclick = function() { confirmResume(token); }; resume.appendChild(button);
    });
  }
  async function start(token) {
    var r = await api('/api/admin/voice_management/cleanup', {method:'POST',body:JSON.stringify({token:token})});
    if (!r.ok) { toast(r.data.error, true); return; }
    pending = token;
    el('voiceCleanupProgress').hidden = false;
    el('voiceCleanupStatus').textContent = 'Cleanup started. New paid work pauses until it finishes.';
    poll();
  }
  async function poll() {
    var r = await api('/api/admin/voice_management/cleanup/' + encodeURIComponent(pending));
    if (!r.ok) { el('voiceCleanupStatus').textContent = r.data.error + ' Refresh the inventory to resume.'; return; }
    el('voiceCleanupProgress').max = r.data.total || 1;
    el('voiceCleanupProgress').value = r.data.deleted;
    el('voiceCleanupStatus').textContent = r.data.deleted + ' of ' + r.data.total + ' deleted' + (r.data.failed ? ' · ' + r.data.failed + ' pending retry' : '') + (r.data.error ? ' · ' + r.data.error : '');
    if (r.data.running) { timer = setTimeout(poll, 1500); }
    else { pending = ''; await refresh(); }
  }
  async function preview(expiredOnly) {
    if (pending) return;
    if (!inventory) { toast('Refresh the voice inventory first.', true); return; }
    var ids = expiredOnly ? inventory.voices.filter(function(r) { return r.selectable && ['expired','orphan'].includes(r.category); }).map(function(r) { return r.id; })
      : Array.from(document.querySelectorAll('.voice-storage-pick:checked')).map(function(e) { return e.value; });
    if (!ids.length) { toast('No voices selected.', true); return; }
    if (ids.length > 100) { toast('Select at most 100 voices per cleanup.', true); return; }
    var r = await api('/api/admin/voice_management/preview', {method:'POST',body:JSON.stringify({ids:ids})});
    if (!r.ok) { toast(r.data.error, true); return; }
    var saved = r.data.voices.filter(function(v) { return v.saved_rows > 0; }).length;
    var text = 'Permanently delete ' + r.data.count + ' voice(s)?\n\n' + r.data.voices.map(function(v) { return v.name || v.id; }).join('\n') +
      '\n\nProject voice references will be cleared. Existing exports, source samples and backgrounds remain. Later corrections may need paid re-cloning.' +
      (saved ? '\n\nThis also removes saved library entries for ' + saved + ' selected voice(s).' : '') +
      '\nShort projects saved on your computer may still contain old voice IDs; choose or clone a voice again when reopening them.';
    if (await LisanDialog.confirm(text)) await start(r.data.token);
  }
  async function confirmResume(token) {
    if (await LisanDialog.confirm('Retry the previously confirmed cleanup? Voices already deleted will not be deleted again.')) await start(token);
  }
  root.loadVoiceStorageSettings = loadSettings;
  root.saveVoiceStorageSettings = saveSettings;
  root.refreshVoiceStorage = refresh;
  root.previewVoiceCleanup = preview;
  root.selectAllVoiceStorage = function(checked) {
    document.querySelectorAll('.voice-storage-pick:not(:disabled)').forEach(function(box) { box.checked = checked; });
  };
  if (typeof module === 'object' && module.exports) module.exports = {table:table, labels:labels};
})(typeof window === 'object' ? window : globalThis);
