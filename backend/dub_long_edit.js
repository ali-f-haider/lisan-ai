/* Completed projects stay intact. Saving a draft never replaces a published dub. */
(function () {
  'use strict';
  var lang = localStorage.getItem('lisanEditLang') || 'en', project = null, state = null;
  var selected = new Set(), page = 0, dirty = false, working = false, pending = null, saveTimer = null, saveFlight = null;
  var $ = function(id) { return document.getElementById(id); };
  var tr = function(en, ar) { return lang === 'ar' ? ar : en; };
  async function api(path, method, body) {
    var options = {method:method || 'GET', credentials:'same-origin'};
    if (body) { options.headers = {'Content-Type':'application/json'}; options.body = JSON.stringify(body); }
    var response = await fetch(path, options), data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Request failed');
    return data;
  }
  function error(ex) { $('error').textContent = ex.message || String(ex); }
  function staticText() {
    document.documentElement.lang = lang; document.documentElement.dir = lang === 'ar' ? 'rtl' : 'ltr';
    $('title').textContent = tr('Correct a long dub', 'تصحيح الدبلجة الطويلة');
    $('back').textContent = tr('Long dubbing', 'الدبلجة الطويلة'); $('appLink').textContent = tr('Main app', 'التطبيق الرئيسي');
    $('language').textContent = lang === 'ar' ? 'English' : 'العربية';
    $('chooseLabel').textContent = tr('Choose a completed project', 'اختر مشروعاً مكتملاً');
    $('intro').textContent = tr('Select only the lines that need another dub. Correct their text, speaker, style or timing, then generate a correction track. Each export keeps the original duration, with no generated voices outside your selected lines.', 'حدد فقط الأسطر التي تحتاج إعادة الدبلجة. صحح النص أو المتحدث أو الأسلوب أو التوقيت ثم أنشئ مسار التصحيحات. يحافظ كل ملف على مدة الأصل، ولا يحتوي على أصوات مولدة خارج الأسطر المحددة.');
    $('review').textContent = tr('✓ Mark these ten lines reviewed', '✓ تمت مراجعة هذه الأسطر العشرة');
    $('dub').textContent = tr('Dub selected lines', 'دبلجة الأسطر المحددة'); $('finish').textContent = tr('Finish corrections', 'إنهاء التصحيحات');
    $('trackHelp').textContent = tr('Download either the corrections with the full music/effects layer, or voices only. Use voices only when keeping the existing background in Premiere, DaVinci or CapCut. Each new pass generates only the lines you select.', 'نزّل التصحيحات مع كامل الموسيقى والمؤثرات، أو الأصوات فقط. استخدم الأصوات فقط عند الإبقاء على الخلفية الموجودة في Premiere أو DaVinci أو CapCut. في كل مرة تُولد الأسطر المحددة فقط.');
    $('recoveryTitle').textContent = tr('Recover editing assets', 'استعادة ملفات التحرير');
    $('recoveryText').textContent = tr('This older project’s voices/references were deleted. Attach the same original file: its fingerprint is checked, and its audio is prepared without retranscribing. Voice re-cloning and any first background repair are shown in the price before generation.', 'حُذفت أصوات وعينات هذا المشروع القديم. أرفق الملف الأصلي نفسه: يتم التحقق من بصمته وتحضير صوته دون إعادة التفريغ. تظهر تكلفة إعادة استنساخ الصوت وإصلاح الخلفية قبل التوليد.');
    $('restore').textContent = tr('Attach original file', 'إرفاق الملف الأصلي');
    $('exportsTitle').textContent = tr('Correction exports', 'ملفات التصحيحات');
  }
  function setBusy(value) { working = value; document.querySelectorAll('#lines input,#lines textarea,#lines select,#pages button').forEach(function(c) { c.disabled = value; }); ['dub','finish','projects','restore','review'].forEach(function(id) { $(id).disabled = value; }); }
  function count() { $('selectedCount').textContent = tr(selected.size + ' selected lines', selected.size + ' أسطر محددة'); }
  function change(row, field, value) {
    row[field] = value; dirty = true; pending = null;
    var index = state.segments.findIndex(function(r) { return r.segment_id === row.segment_id; });
    var batch = (state.batches || []).find(function(b) { return b.page === Math.floor(index / 10); });
    if (batch) batch.reviewed = false;
    renderPages(); $('saveState').textContent = tr('Unsaved changes', 'تغييرات غير محفوظة');
    clearTimeout(saveTimer); saveTimer = setTimeout(function() { save().catch(error); }, 900);
  }
  async function save() {
    if (saveFlight) { await saveFlight; return save(); }
    saveFlight = saveOnce();
    try { await saveFlight; } finally { saveFlight = null; }
    if (dirty) return save();
  }
  async function saveOnce() {
    clearTimeout(saveTimer);
    if (!dirty || !project) return;
    var id = project.id, snapshot = JSON.stringify(state.segments);
    var data = await api('/api/longdub/' + id + '/corrections', 'PUT', {edits:JSON.parse(snapshot)});
    if (project.id !== id) return;
    if (JSON.stringify(state.segments) === snapshot) { state = data; dirty = false; $('saveState').textContent = tr('Saved', 'تم الحفظ'); renderPages(); }
    else { dirty = true; }
  }
  function renderPages() {
    $('pages').replaceChildren();
    (state.batches || []).forEach(function(batch) {
      var button = document.createElement('button'); button.type = 'button';
      button.textContent = (batch.reviewed ? '✓ ' : '') + batch.first + '–' + batch.last;
      button.classList.toggle('checked', batch.reviewed); button.setAttribute('aria-current', batch.page === page ? 'page' : 'false');
      button.onclick = async function() { try { await save(); page = batch.page; renderLines(); } catch(ex) { error(ex); } };
      $('pages').appendChild(button);
    });
  }
  function input(parent, label, type, value, onChange) {
    var wrap = document.createElement('label'); wrap.textContent = label;
    var control = document.createElement(type === 'textarea' ? 'textarea' : 'input');
    if (type !== 'textarea') control.type = type;
    if (type === 'number') { control.step = '.01'; control.min = 0; control.max = state.duration; }
    if (type === 'textarea') control.maxLength = 2000;
    control.value = value; control.oninput = function() { onChange(type === 'number' ? Number(control.value) : control.value); };
    wrap.appendChild(control); parent.appendChild(wrap); return control;
  }
  function renderLines() {
    renderPages(); $('lines').replaceChildren();
    state.segments.slice(page * 10, page * 10 + 10).forEach(function(row) {
      var line = document.createElement('div'); line.className = 'line';
      var tools = document.createElement('div'); var selectLabel = document.createElement('label');
      var box = document.createElement('input'); box.type = 'checkbox'; box.checked = selected.has(row.segment_id);
      box.onchange = function() { if (box.checked) selected.add(row.segment_id); else selected.delete(row.segment_id); pending = null; count(); };
      selectLabel.append(box, document.createTextNode(tr(' Correct this line', ' تصحيح هذا السطر'))); tools.appendChild(selectLabel);
      input(tools, tr('Start (seconds)', 'البداية بالثواني'), 'number', row.start, function(v) { change(row, 'start', v); });
      input(tools, tr('End (seconds)', 'النهاية بالثواني'), 'number', row.end, function(v) { change(row, 'end', v); });
      var speaker = document.createElement('select');
      state.speaker_list.forEach(function(s) { var option = document.createElement('option'); option.value = s.id; option.textContent = s.name; speaker.appendChild(option); });
      speaker.value = row.speaker_id; speaker.setAttribute('aria-label', tr('Speaker', 'المتحدث')); speaker.onchange = function() { change(row, 'speaker_id', speaker.value); }; tools.appendChild(speaker);
      input(tools, tr('Speaking style', 'أسلوب الكلام'), 'text', row.emotion || 'neutral', function(v) { change(row, 'emotion', v); });
      if ((state.overlaps[row.segment_id] || []).length) { var warn = document.createElement('p'); warn.className = 'warning'; warn.textContent = tr('⚠ Overlaps another line. Check both timings.', '⚠ يتداخل مع سطر آخر. راجع توقيت السطرين.'); tools.appendChild(warn); }
      var en = document.createElement('div'), ar = document.createElement('div');
      input(en, tr('Original text', 'النص الأصلي'), 'textarea', row.text || '', function(v) { change(row, 'text', v); }).dir = 'ltr';
      input(ar, tr('Arabic text', 'النص العربي'), 'textarea', row.arabic_text || '', function(v) { change(row, 'arabic_text', v); }).dir = 'rtl';
      line.append(tools, en, ar); $('lines').appendChild(line);
    }); count();
  }
  function renderHistory() {
    $('history').replaceChildren(); $('exports').classList.toggle('hidden', !state.history.length);
    state.history.forEach(function(job) {
      var item = document.createElement('div'); var label = document.createElement('p'); label.textContent = job.name + ' — ' + job.status; item.appendChild(label);
      if (job.status === 'done' && job.file_available) {
        [['download',tr('Music/effects + selected voices', 'الموسيقى والمؤثرات مع الأصوات المحددة')],['track/voices',tr('Selected voices only', 'الأصوات المحددة فقط')]].forEach(function(pair) {
          var link = document.createElement('a'); link.href = '/api/longdub/' + job.id + '/' + pair[0]; link.textContent = pair[1]; link.style.marginInlineEnd = '18px'; item.appendChild(link);
        });
      }
      if (job.error) { var note = document.createElement('p'); note.className = 'error'; note.textContent = job.error; item.appendChild(note); }
      $('history').appendChild(item);
    });
  }
  async function open(id) {
    if (dirty) await save();
    selected.clear(); page = 0; pending = null;
    project = {id:id}; state = await api('/api/longdub/' + id + '/corrections');
    $('editor').classList.remove('hidden'); $('recovery').classList.toggle('hidden', state.has_assets);
    $('error').textContent = ''; renderLines(); renderHistory();
    if (state.busy) poll();
  }
  async function poll() {
    if (!project) return;
    var id = project.id, data = await api('/api/longdub/' + id + '/corrections');
    if (!project || project.id !== id) return;
    state.history = data.history; renderHistory();
    var busy = data.history.find(function(j) { return j.status === 'payment_pending' || j.status === 'dubbing' || j.status === 'confirmed'; });
    $('progress').textContent = busy ? busy.message + ' ' + (busy.percent || 0) + '%' : '';
    state.busy = !!busy;
    setBusy(!!busy);
    if (busy) setTimeout(function() { poll().catch(error); }, 3000);
    else { state = data; renderLines(); }
  }
  $('dub').onclick = async function() {
    $('error').textContent = ''; setBusy(true);
    try {
      await save(); var ids = Array.from(selected); var id = project.id;
      pending = await api('/api/longdub/' + id + '/corrections/quote', 'POST', {selected:ids});
      var text = tr('Generate ' + ids.length + ' selected lines for ' + pending.due + ' credits?', 'إنشاء ' + ids.length + ' أسطر محددة مقابل ' + pending.due + ' رصيداً؟');
      if (pending.music.max_credits) text += tr('\nMusic recovery: up to ' + pending.music.max_credits + ' additional credits, charged only for successful repairs.', '\nاستعادة الموسيقى: بحد أقصى ' + pending.music.max_credits + ' رصيداً إضافياً؛ تُحاسب فقط على الإصلاحات الناجحة.');
      var confirmed = window.LisanDialog ? await LisanDialog.confirm(text) : window.confirm(text);
      if (!confirmed) return;
      await api('/api/longdub/' + id + '/corrections/dub', 'POST', {selected:ids, token:pending.token});
      await poll();
    } catch(ex) { error(ex); } finally { if (!state.busy) setBusy(false); }
  };
  $('review').onclick = async function() { try { await save(); var r = await api('/api/longdub/' + project.id + '/review','POST',{page:page,correction:true}); state.batches = r.batches; renderPages(); } catch(ex) { error(ex); } };
  $('finish').onclick = async function() {
    try {
      var text = tr('Release the saved cloned voices? You can return later, but re-cloning from the saved samples will cost credits.', 'هل تريد تحرير الأصوات المستنسخة المحفوظة؟ يمكنك العودة لاحقاً لكن إعادة الاستنساخ من العينات المحفوظة تتطلب رصيداً.');
      var yes = window.LisanDialog ? await LisanDialog.confirm(text) : window.confirm(text);
      if (yes) { await api('/api/longdub/' + project.id + '/corrections/finish','POST',{}); $('progress').textContent = tr('Cloned voices released. Your exports and reference samples remain saved.', 'تم تحرير الأصوات المستنسخة. تظل الملفات والعينات المرجعية محفوظة.'); }
    } catch(ex) { error(ex); }
  };
  $('restore').onclick = async function() {
    var file = $('original').files[0]; if (!file || !project) return;
    setBusy(true);
    try {
      var restore = await api('/api/longdub/' + project.id + '/corrections/restore','POST',{filename:file.name,size:file.size});
      var chunk = restore.chunk_bytes || 8 * 1024 * 1024;
      for (var at = 0, index = 0; at < file.size; at += chunk, index++) {
        var response = await fetch('/api/longdub/' + restore.id + '/chunk?index=' + index, {method:'PUT',body:file.slice(at,at+chunk)});
        if (!response.ok) throw new Error((await response.json()).error || 'Upload failed');
        $('restoreStatus').textContent = tr('Uploading original… ', 'جارٍ رفع الأصل… ') + Math.round((at+chunk)/file.size*100) + '%';
      }
      await api('/api/longdub/' + restore.id + '/finish','POST',{});
      var watch = async function() {
        var job = await api('/api/longdub/' + restore.id); $('restoreStatus').textContent = job.message;
        if (job.status === 'failed') throw new Error(job.error);
        if (job.status === 'editing') { $('restoreStatus').textContent = tr('Original audio recovered. Select lines and review the correction price.', 'تمت استعادة الصوت الأصلي. حدد الأسطر وراجع سعر التصحيح.'); setBusy(false); }
        else setTimeout(function() { watch().catch(function(ex) { error(ex); setBusy(false); }); }, 3000);
      }; await watch();
    } catch(ex) { error(ex); setBusy(false); }
  };
  $('projects').onchange = function() { if (this.value) open(this.value).catch(error); };
  $('language').onclick = function() { lang = lang === 'en' ? 'ar' : 'en'; localStorage.setItem('lisanEditLang',lang); staticText(); if(state) renderLines(); };
  $('helpButton').onclick = function() { this.parentElement.classList.toggle('open'); };
  window.addEventListener('beforeunload',function(e) { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
  staticText();
  api('/api/longdub').then(function(data) {
    $('balance').textContent = data.credits == null ? tr('Balance unavailable', 'الرصيد غير متاح') : data.credits + tr(' credits', ' رصيداً');
    $('projects').replaceChildren(); var initial = document.createElement('option'); initial.value = ''; initial.textContent = tr('Choose a project…','اختر مشروعاً…'); $('projects').appendChild(initial);
    data.jobs.filter(function(j) { return j.status === 'done' && !j.edit_of; }).forEach(function(job) { var option = document.createElement('option'); option.value = job.id; option.textContent = job.name; $('projects').appendChild(option); });
  }).catch(function(ex) { $('balance').textContent = ex.message; });
})();
