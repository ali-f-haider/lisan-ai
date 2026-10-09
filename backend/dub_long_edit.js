var EMOTIONS = ["neutral", "happy", "sad", "angry", "fearful", "surprised", "disgusted", "shouting", "whispering", "screaming", "yelling", "crying", "laughing", "sarcastic", "seductive", "narrative", "announcer", "conversational", "depressed", "anxious", "confident", "indifferent", "excited", "serious", "playful", "terrified", "relieved", "thoughtful", "mocking", "pleading", "commanding", "slowly", "rushed", "drawn out", "hesitant", "stammering", "softly", "booming", "sorrowful", "frustrated", "annoyed", "appalled", "awe", "regretful", "resigned", "curious", "deadpan", "tired", "sneezing", "coughing", "sighing", "gasping", "laugh", "sigh", "clear throat", "yawn", "very fast", "very quiet", "high pitch"];
var EMO_AR = {"neutral": "محايد", "happy": "سعيد", "sad": "حزين", "angry": "غاضب", "fearful": "خائف", "surprised": "متفاجئ", "disgusted": "مشمئز", "shouting": "يصيح", "whispering": "يهمس", "screaming": "يصرخ", "yelling": "ينادي بصوت عالٍ", "crying": "يبكي", "laughing": "يضحك أثناء الكلام", "sarcastic": "ساخر", "seductive": "مُغرٍ", "narrative": "سردي", "announcer": "مذيع", "conversational": "حواري", "depressed": "مكتئب", "anxious": "قلق", "confident": "واثق", "indifferent": "لامبالٍ", "excited": "متحمس", "serious": "جاد", "playful": "مرح", "terrified": "مرعوب", "relieved": "مرتاح", "thoughtful": "متأمل", "mocking": "مستهزئ", "pleading": "متوسل", "commanding": "آمر", "slowly": "ببطء", "rushed": "مستعجل", "drawn out": "مع مدّ الكلمات", "hesitant": "متردد", "stammering": "متلعثم", "softly": "بصوت خافت", "booming": "بصوت جهوري", "sorrowful": "كئيب", "frustrated": "محبط", "annoyed": "منزعج", "appalled": "مصدوم", "awe": "مبهور", "regretful": "نادم", "resigned": "مستسلم", "curious": "فضولي", "deadpan": "ببرود", "tired": "متعب", "sneezing": "يعطس", "coughing": "يسعل", "sighing": "يتنهد", "gasping": "يشهق", "laugh": "ضحكة", "sigh": "تنهيدة", "clear throat": "تنحنح", "yawn": "تثاؤب", "very fast": "سريع جداً", "very quiet": "هادئ جداً", "high pitch": "نبرة عالية"};
/* Correction edits never replace the original transcript or published dub. */
(function () {
  'use strict';
  var lang = localStorage.getItem('lisan_lang') === 'ar' ? 'ar' : 'en', project = null, state = null;
  var selected = new Set(), page = 0, dirty = false, working = false, saveTimer = null, saveFlight = null;
  var manualNoticeId = '', notified = new Set(), pollTimer = null; var meta = {}, listen = null;
  var $ = function(id) { return document.getElementById(id); };
  var tr = function(en, ar) { return lang === 'ar' ? ar : en; };
  async function api(path, method, body) {
    var options = {method:method || 'GET', credentials:'same-origin'};
    if (body) { options.headers = {'Content-Type':'application/json'}; options.body = JSON.stringify(body); }
    var response = await fetch(path, options), data = await response.json();
    if (!response.ok) throw new Error(data.error || tr('Request failed.', 'تعذّر تنفيذ الطلب.'));
    return data;
  }
  function error(ex) {
    $('error').textContent = lang === 'ar' ? 'تعذّر إكمال الخطوة. تحقق من التوقيت والملف الأصلي ثم حاول مرة أخرى.' : ex.message || String(ex);
  }
  function notify(type, message) {
    var item = document.createElement('div'); item.className = 'notify ' + type;
    var text = document.createElement('div'); text.className = 'msg'; text.textContent = message;
    var close = document.createElement('button'); close.type = 'button'; close.textContent = tr('OK', 'حسناً'); close.onclick = function() { item.remove(); };
    item.append(text, close); $('notifyPanel').appendChild(item); $('notifyPanel').style.display = 'flex';
    setTimeout(function() { item.remove(); if (!$('notifyPanel').children.length) $('notifyPanel').style.display = 'none'; }, 10000);
  }
  function staticText() {
    document.documentElement.lang = lang; document.documentElement.dir = lang === 'ar' ? 'rtl' : 'ltr';
    document.title = tr('Lisan AI — Correct a long dub', 'لسان — تصحيح الدبلجة الطويلة');
    $('title').textContent = document.title;
    $('back').textContent = tr('← Long dubbing', '← الدبلجة الطويلة'); $('appLink').textContent = tr('← Back to App', '← العودة إلى التطبيق');
    $('logout').textContent = tr('Log Out', 'تسجيل الخروج'); $('creditsWord').textContent = tr('credits', 'رصيد');
    $('language').textContent = lang === 'ar' ? 'English' : 'العربية';
    $('chooseLabel').textContent = tr('Choose a completed project', 'اختر مشروعاً مكتملاً');
    $('intro').textContent = tr('Select the lines that need another dub, correct their text or timing, and generate a correction track. Each export keeps the original duration, with no generated voices outside the selected lines.', 'حدد الأسطر التي تحتاج إعادة الدبلجة، وصحح النص أو التوقيت ثم أنشئ مسار التصحيحات. يحافظ كل ملف على مدة الأصل، ولا يحتوي على أصوات مولدة خارج الأسطر المحددة.');
    $('dub').textContent = tr('Dub selected lines', 'دبلجة الأسطر المحددة'); $('finish').textContent = tr('Finish corrections', 'إنهاء التصحيحات');
    $('trackHelp').textContent = tr('Use selected voices only when keeping your existing background in Premiere, DaVinci or CapCut. Each new pass generates only the lines you select.', 'استخدم الأصوات المحددة فقط عند الإبقاء على الخلفية الموجودة في برنامج المونتاج. في كل مرة تُولد الأسطر المحددة فقط.');
    $('recoveryTitle').textContent = tr('Attach the original for editing', 'إرفاق الأصل للتحرير');
    $('recoveryText').textContent = tr('Attach the same original file if voice references or the background are missing, or to listen to the original audio. It is verified without retranscribing. Any required re-cloning or background repair is included in the generation price.', 'أرفق الملف الأصلي نفسه عند غياب عينات الصوت أو الخلفية، أو للاستماع إلى الصوت الأصلي. يتم التحقق منه دون إعادة التفريغ. تُحتسب إعادة الاستنساخ وإصلاح الخلفية عند الحاجة ضمن سعر التوليد.');
    $('restore').textContent = tr('Attach original file', 'إرفاق الملف الأصلي');
    $('pickFile').textContent = tr('Choose original file', 'اختيار الملف الأصلي'); $('openProject').textContent = tr('Open', 'فتح');
    $('pickHint').textContent = tr('Choose the film, then your original file, then press Open. The file is only played in your browser, nothing is uploaded and nothing costs credits.', 'اختر الفيلم، ثم ملفك الأصلي، ثم اضغط فتح. يُشغَّل الملف في متصفحك فقط، ولا يُرفع شيء ولا يكلّف أي رصيد.');
    $('exportsTitle').textContent = tr('Correction exports', 'ملفات التصحيحات');
    $('timeHeading').textContent = tr('Timing and speaker', 'التوقيت والمتحدث');
    $('originalHeading').textContent = tr('Original text', 'النص الأصلي'); $('arabicHeading').textContent = tr('Arabic text', 'النص العربي');
  }
  function setBusy(value) {
    working = value;
    document.querySelectorAll('#lines input,#lines textarea,#lines select,#lines button,#pages button,#pages input,#pagesBottom button,#pagesBottom input').forEach(function(c) { c.disabled = value; });
    ['dub','finish','projects','restore','pickFile','openProject'].forEach(function(id) { $(id).disabled = value; });
  }
  function count() { $('selectedCount').textContent = tr(selected.size + ' selected lines', selected.size + ' أسطر محددة'); }
  function invalidate(row) {
    var index = state.segments.findIndex(function(r) { return r.segment_id === row.segment_id; });
    var batch = (state.batches || []).find(function(b) { return b.page === Math.floor(index / 10); });
    if (batch) batch.reviewed = false;
  }
  function speakerBadge(row){if(row.speaker_confidence!=='low')return null;var names={short_reply:['Short reply','رد قصير'],voters_split:['Not sure who says this line','لسنا متأكدين ممن يقول هذا السطر'],candidate_rejected:['May belong to another voice we could not confirm','قد يعود إلى صوت آخر لم نتمكن من تأكيده'],overlapping_speech:['Voices overlap','تداخل أصوات'],no_detected_turn:['Speech outside the detected turns','كلام خارج الأدوار المكتشفة'],word_in_gap:['Speech outside the detected turns','كلام خارج الأدوار المكتشفة'],speaker_not_supported:['The detected voice may belong to someone else','قد يكون الصوت لمتحدث آخر'],word_speaker_disagrees:['The detected voice may belong to someone else','قد يكون الصوت لمتحدث آخر'],weak_time_coverage:['The detected voice may belong to someone else','قد يكون الصوت لمتحدث آخر'],smoothed_assignment:['The detected voice may belong to someone else','قد يكون الصوت لمتحدث آخر']};var seen={},list=[];(row.speaker_reasons||[]).forEach(function(k){var n=names[k];if(n){var t=tr(n[0],n[1]);if(!seen[t]){seen[t]=1;list.push(t);}}});var b=document.createElement('span');b.className='ld-spk-check';b.tabIndex=0;b.textContent=tr('Check speaker','راجع المتحدث');b.title=list.length?list.join(' · '):tr('Check who says this line','راجع من يقول هذا السطر');b.setAttribute('aria-label',b.textContent+(list.length?': '+list.join(', '):''));return b;}
  function change(row, field, value) {
    row[field] = value;
    if (field === 'emotion') row.emotion_set = true;
    dirty = true; invalidate(row);
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
    if (!project || project.id !== id) return;
    if (JSON.stringify(state.segments) === snapshot) {
      // Keep the row objects bound to the controls; replacing them loses the next keystroke after autosave.
      var reordered = data.segments.map(function(r) { return r.segment_id; }).join('|') !== state.segments.map(function(r) { return r.segment_id; }).join('|');
      var active = document.activeElement && document.activeElement.closest('.ld-seg');
      var anchor = active ? active.dataset.id : state.segments[page*10] && state.segments[page*10].segment_id;
      var byId = new Map(state.segments.map(function(r) { return [r.segment_id,r]; }));
      state.segments = data.segments.map(function(r) { return byId.get(r.segment_id); });
      state.batches = data.batches; state.overlaps = data.overlaps; dirty = false;
      $('saveState').textContent = tr('Saved', 'تم الحفظ');
      if(reordered) { page = Math.max(0,Math.floor(state.segments.findIndex(function(r) { return r.segment_id===anchor; })/10)); renderLines(); }
      else { renderPages(); refreshOverlaps(); }
    }
  }
  async function markBatch(batch, checked) {
    if (working) return;
    setBusy(true);
    try {
      await save();
      var result = await api('/api/longdub/' + project.id + '/review', 'POST', {page:batch.page,correction:true,reviewed:checked});
      state.batches = result.batches;
    } catch(ex) { error(ex); }
    finally { setBusy(false); renderPages(); }
  }
  function renderPages() {
    ['pages','pagesBottom'].forEach(function(id) {
      $(id).replaceChildren();
      (state.batches || []).forEach(function(batch) {
        var group = document.createElement('span'); group.className = 'ld-page-group'; group.classList.toggle('reviewed', batch.reviewed);
        var button = document.createElement('button'); button.type = 'button'; button.className = 'btn-plain';
        button.textContent = batch.first + '–' + batch.last; button.disabled = working;
        button.setAttribute('aria-current', batch.page === page ? 'page' : 'false');
        button.onclick = async function() { if (working) return; try { await save(); state.segments.sort(function(a,b) { return a.start-b.start; }); page = batch.page; renderLines(); } catch(ex) { error(ex); } };
        var label = document.createElement('label'), check = document.createElement('input'); check.type = 'checkbox'; check.checked = batch.reviewed; check.disabled = working;
        label.title = tr('These lines are reviewed', 'تمت مراجعة هذه الأسطر'); check.setAttribute('aria-label', label.title + ' ' + button.textContent);
        check.onchange = function() { markBatch(batch, check.checked); };
        label.appendChild(check); group.append(button, label); $(id).appendChild(group);
      });
    });
  }
  function button(parent, en, ar, callback, danger) {
    var control = document.createElement('button'); control.type = 'button'; control.className = danger ? 'btn-danger' : 'btn-plain'; control.textContent = tr(en, ar); control.onclick = callback; parent.appendChild(control); return control;
  }
  function refreshOverlaps() {
    document.querySelectorAll('#lines .ld-seg').forEach(function(element) {
      var overlaps = (state.overlaps[element.dataset.id] || []).length > 0;
      element.classList.toggle('ld-overlap', overlaps); var warning = element.querySelector('.ld-overlap-warning'); if(warning) warning.classList.toggle('hidden', !overlaps);
    });
  }
  function timeInput(parent, row, field, en, ar) {
    var wrap = document.createElement('label'); wrap.className = 'ld-tf'; var title = document.createElement('span'); title.textContent = tr(en, ar);
    var input = document.createElement('input'); input.type = 'number'; input.step = '.001'; input.min = 0; input.max = state.duration; input.value = row[field];
    input.setAttribute('aria-label', tr(en, ar)); input.onchange = function() { change(row,field,Number(input.value)); };
    wrap.append(title,input); parent.appendChild(wrap);
  }
  function stylePicker(parent,row) {
    var shell = document.createElement('details'); shell.className = 'ld-edit-style';
    var summary = document.createElement('summary'); shell.appendChild(summary); var menu = document.createElement('div'); shell.appendChild(menu);
    var tags = (row.emotion || 'neutral').split(',').map(function(t) { return t.trim(); });
    function refresh() { summary.textContent = tags.map(function(t) { return lang==='ar' ? EMO_AR[t] || t : t.charAt(0).toUpperCase()+t.slice(1); }).join(lang==='ar' ? '، ' : ', '); }
    EMOTIONS.forEach(function(tag) {
      var label=document.createElement('label'), box=document.createElement('input'); box.type='checkbox'; box.checked=tags.includes(tag);
      label.append(box,document.createTextNode(lang==='ar' ? EMO_AR[tag] || tag : tag.charAt(0).toUpperCase()+tag.slice(1)));
      box.onchange=function() {
        if(working) { box.checked=!box.checked; return; }
        if(box.checked && tags.filter(function(t){return t!=='neutral';}).length>=3 && tag!=='neutral') { box.checked=false; return; }
        tags=box.checked ? (tag==='neutral' ? ['neutral'] : tags.filter(function(t){return t!=='neutral';}).concat(tag)) : tags.filter(function(t){return t!==tag;});
        if(!tags.length) tags=['neutral'];
        menu.querySelectorAll('input').forEach(function(c,i){c.checked=tags.includes(EMOTIONS[i]);}); change(row,'emotion',tags.join(', ')); refresh();
      };
      menu.appendChild(label);
    }); refresh(); parent.appendChild(shell);
  }
  function waqfPicker(parent,row) {
    var wrap=document.createElement('label'); wrap.className='ld-waqf'; wrap.title=tr('How the voice ends this line: Auto decides from the punctuation and the pause after it; Stop ends it as a full stop; Join lets it flow into the next line. The text itself is never changed.','كيف ينطق الصوت نهاية السطر: تلقائي يقرّر من علامات الترقيم والوقفة بعده؛ وقف ينهيه كوقفة تامة؛ وصل يتركه متصلاً بالسطر التالي. النص نفسه لا يتغيّر.');
    var select=document.createElement('select'); select.setAttribute('aria-label',tr('Line ending','نهاية السطر'));
    [['auto','Auto','تلقائي'],['stop','Stop','وقف'],['join','Join','وصل']].forEach(function(o){var opt=document.createElement('option'); opt.value=o[0]; opt.textContent=tr(o[1],o[2]); select.appendChild(opt);});
    select.value=row.waqf||'auto'; select.onchange=function(){ if(working){select.value=row.waqf||'auto';return;} change(row,'waqf',select.value); };
    var title=document.createElement('span'); title.textContent=tr('Line ending','نهاية السطر');
    wrap.append(title,select); parent.appendChild(wrap);
  }
  async function action(row,operation,extra) {
    if(working) return;
    setBusy(true); $('error').textContent='';
    try {
      await save(); var data=await api('/api/longdub/'+project.id+'/corrections/line/'+operation,'POST',Object.assign({segment_id:row.segment_id},extra || {}));
      state=data; dirty=false;
      var existing=new Set(state.segments.map(function(r){return r.segment_id;})); selected.forEach(function(id){if(!existing.has(id))selected.delete(id);});
      if(data.new_id) {
        selected.add(data.new_id); if(operation==='split') selected.add(row.segment_id);
        manualNoticeId=data.new_id; page=Math.floor(state.segments.findIndex(function(r){return r.segment_id===data.new_id;})/10);
        notify('info',tr('Enter the start and end times manually for precise placement.','أدخل وقت البداية والنهاية يدوياً لتحديد موضع السطر بدقة.'));
      }
      $('saveState').textContent=tr('Saved','تم الحفظ'); renderLines();
    } catch(ex) { error(ex); }
    finally { setBusy(false); }
  }
  async function checkStyle(row) {
    if(working) return; setBusy(true);
    try {
      await save(); var result=await api('/api/longdub/'+project.id+'/corrections/emotion','POST',{segment_id:row.segment_id});
      if(!result.fallback) { notify('info',tr('The style could not be heard clearly in this clip (too short or unclear), so the current style was kept.','لم يتبيّن الأسلوب بوضوح من هذا المقطع (قصير جدًا أو غير واضح)، لذلك أُبقي الأسلوب الحالي.')); return; }
      var style=lang==='ar' ? EMO_AR[result.fallback] || result.fallback : result.fallback;
      var text=tr('Suggested style: ','الأسلوب المقترح: ')+style+(lang==='en' ? '\n'+result.reason : '')+'\n'+tr('Apply this suggestion?','هل تريد تطبيق هذا الاقتراح؟');
      if(await LisanDialog.confirm(text)) { change(row,'emotion',result.fallback); await save(); renderLines(); }
    } catch(ex) { error(ex); }
    finally { setBusy(false); }
  }
  function renderLines() {
    page=Math.max(0,Math.min(page,Math.ceil(state.segments.length/10)-1)); renderPages(); $('lines').replaceChildren();
    state.segments.slice(page*10,page*10+10).forEach(function(row,index) {
      var line=document.createElement('div'); line.className='ld-seg'; line.dataset.id=row.segment_id;
      var tools=document.createElement('div'); tools.className='ld-time';
      var label=document.createElement('label'); label.className='ld-select-line'; var box=document.createElement('input'); box.type='checkbox'; box.checked=selected.has(row.segment_id);
      box.onchange=function(){if(box.checked)selected.add(row.segment_id);else selected.delete(row.segment_id);count();}; label.append(box,document.createTextNode(tr('Correct line ','تصحيح السطر ')+(page*10+index+1))); tools.appendChild(label);
      timeInput(tools,row,'start','Start','البداية'); timeInput(tools,row,'end','End','النهاية');
      var speaker=document.createElement('select'); state.speaker_list.forEach(function(s){var opt=document.createElement('option');opt.value=s.id;opt.textContent=s.name;speaker.appendChild(opt);}); speaker.value=row.speaker_id;speaker.setAttribute('aria-label',tr('Speaker','المتحدث'));speaker.onchange=function(){change(row,'speaker_id',speaker.value);if(badge){badge.remove();badge=null;}};tools.appendChild(speaker);var badge=speakerBadge(row);if(badge)tools.appendChild(badge);
      var warning=document.createElement('p');warning.className='ld-time-notice ld-overlap-warning hidden';warning.textContent=tr('⚠ Overlaps another line. Check both timings.','⚠ يتداخل مع سطر آخر. راجع توقيت السطرين.');tools.appendChild(warning);
      if(manualNoticeId===row.segment_id){var notice=document.createElement('p');notice.className='ld-time-notice';notice.setAttribute('role','status');notice.textContent=tr('Set both times manually for precise placement.','أدخل الوقتين يدوياً لتحديد الموضع بدقة.');tools.appendChild(notice);}
      var original=document.createElement('textarea'); original.className='en'; original.dir='ltr'; original.maxLength=2000; original.value=row.text||''; original.setAttribute('aria-label',tr('Original text','النص الأصلي')); original.oninput=function(){change(row,'text',original.value);};
      var caret=false; ['click','keyup','select'].forEach(function(event){original.addEventListener(event,function(){caret=true;});});
      var controls=document.createElement('div');controls.className='ld-lbtns';tools.appendChild(controls);
      button(controls,'Enter man.','إدخال يدوي',function(){openPlayer(row);});
      button(controls,'Split at cursor','تقسيم عند المؤشر',function(){if(!caret){notify('info',tr('Click between two words in the original text, then split.','انقر بين كلمتين في النص الأصلي ثم اضغط التقسيم.'));return;}action(row,'split',{position:original.selectionStart});});
      button(controls,'+ Insert a line after','+ إدراج سطر بعده',function(){action(row,'insert');});
      button(controls,'Delete line','حذف السطر',async function(){if(await LisanDialog.confirm(tr('Delete this correction line? The first dub stays saved.','حذف هذا السطر من التصحيحات؟ تظل الدبلجة الأولى محفوظة.'),{danger:true}))action(row,'delete');},true);
      var wrap=document.createElement('div');wrap.className='ld-arwrap';var arabic=document.createElement('textarea');arabic.className='ar';arabic.dir='rtl';arabic.maxLength=2000;arabic.value=row.arabic_text||'';arabic.classList.toggle('missing',!arabic.value.trim());arabic.setAttribute('aria-label',tr('Arabic text','النص العربي'));
      var foot=document.createElement('div');foot.className='ld-arfoot';var countText=document.createElement('span');countText.className='ld-cnt';function updateCount(){countText.textContent=arabic.value.length+tr(' characters',' حرفاً');}updateCount();arabic.oninput=function(){change(row,'arabic_text',arabic.value);arabic.classList.toggle('missing',!arabic.value.trim());updateCount();};
      var right=document.createElement('div');right.className='ld-foot-r';stylePicker(right,row);waqfPicker(right,row);
      button(right,'Check style from audio','فحص الأسلوب بالصوت',function(){checkStyle(row);});
      button(right,'Tashkeel','تشكيل',function(){action(row,'tashkeel');});button(right,'Translate again','أعد الترجمة',function(){action(row,'retranslate');});
      foot.append(countText,right);wrap.append(arabic,foot);line.append(tools,original,wrap);$('lines').appendChild(line);
    });count();refreshOverlaps();setBusy(working);
  }
  function statusText(job) {
    if(lang==='en')return job.message||job.status;
    return ({done:'اكتملت التصحيحات',failed:'تعذّر إكمال التصحيحات',dubbing:'جارٍ توليد التصحيحات',confirmed:'التصحيحات في قائمة الانتظار',payment_pending:'جارٍ التحقق من الرصيد'})[job.status]||'جارٍ معالجة المشروع';
  }
  function cost(job) { return Object.values(job.paid||{}).reduce(function(sum,value){return sum+(Number.isFinite(value)?value:0);},0); }
  function renderHistory() {
    $('history').replaceChildren();$('exports').classList.toggle('hidden',!state.history.length);
    state.history.forEach(function(job){
      var item=document.createElement('div');item.className='ld-list-item';var content=document.createElement('div'),label=document.createElement('strong');label.textContent=job.name;content.appendChild(label);var status=document.createElement('p');status.className='ld-plain-note';status.textContent=statusText(job)+(job.status==='done' ? tr(' · Cost: ',' · التكلفة: ')+cost(job)+tr(' credits',' رصيد') : '');content.appendChild(status);
      if(job.status==='done'&&job.file_available){var links=document.createElement('div');links.className='ld-downloads';[['download',tr('Music/effects + selected voices','الموسيقى والمؤثرات مع الأصوات المحددة')],['track/voices',tr('Selected voices only','الأصوات المحددة فقط')]].forEach(function(pair){var link=document.createElement('a');link.className='ld-dl';link.href='/api/longdub/'+job.id+'/'+pair[0];link.textContent=pair[1];links.appendChild(link);});content.appendChild(links);}
      if(job.error){var note=document.createElement('p');note.className='ld-err';note.textContent=lang==='ar' ? 'تعذّر إكمال هذا التصدير. راجع الملف الأصلي والتوقيت.' : job.error;content.appendChild(note);}
      item.appendChild(content);$('history').appendChild(item);
    });
  }
  function progress(job,override) {
    var percent=Math.max(0,Math.min(100,Number(job.percent)||0));$('genProgress').classList.remove('hidden');$('progressFill').style.width=percent+'%';$('progressBar').setAttribute('aria-valuenow',String(percent));$('progress').textContent=percent+'% — '+(override||statusText(job));
  }
  async function refreshCredits() { var data=await api('/api/longdub');$('balance').textContent=data.credits==null ? '…' : data.credits; }
  async function open(id) {
    if(working)return;setBusy(true);
    try{if(dirty)await save();clearTimeout(pollTimer);selected.clear();page=0;manualNoticeId='';project={id:id};state=null;$('editor').classList.add('hidden');$('recovery').classList.add('hidden');
      state=await api('/api/longdub/'+id+'/corrections');notified=new Set(state.history.filter(function(j){return j.status==='done';}).map(function(j){return j.id;}));$('editor').classList.remove('hidden');$('recovery').classList.toggle('hidden',state.has_assets&&state.can_play);$('error').textContent='';$('genProgress').classList.add('hidden');renderLines();renderHistory();if(state.busy)await poll();}
    finally{if(!state||!state.busy)setBusy(false);}
  }
  async function poll() {
    if(!project)return;var id=project.id,data;
    try{data=await api('/api/longdub/'+id+'/corrections');}
    catch(ex){if(project&&project.id===id){error(ex);pollTimer=setTimeout(function(){poll().catch(error);},3000);}return;}
    if(!project||project.id!==id)return;$('error').textContent='';
    state.history=data.history;renderHistory();var busy=data.history.find(function(j){return ['payment_pending','dubbing','confirmed'].includes(j.status);});state.busy=!!busy;setBusy(!!busy);
    if(busy){progress(busy);pollTimer=setTimeout(function(){poll().catch(error);},3000);}
    else{var finished=data.history.filter(function(j){return j.status==='done'&&!notified.has(j.id);});finished.forEach(function(j){notified.add(j.id);notify('success',tr('Correction tracks ready. Cost: ','ملفات التصحيحات جاهزة. التكلفة: ')+cost(j)+tr(' credits.',' رصيد.'));progress(j);});var failed=data.history.find(function(j){return j.status==='failed';});if(!finished.length&&failed)progress(failed);state=data;renderLines();await refreshCredits().catch(function(){$('balance').textContent='…';});}
  }
  $('dub').onclick=async function(){
    if(working)return;$('error').textContent='';setBusy(true);
    try{
      await save();var ids=Array.from(selected),id=project.id;
      var quote=await api('/api/longdub/'+id+'/corrections/quote','POST',{selected:ids});
      if(!Number.isInteger(quote.max_total)||quote.max_total<1)throw new Error(tr('The price is unavailable.','السعر غير متاح.'));
      if(ids.length>1){var question=tr('Dub '+ids.length+' selected lines for up to '+quote.max_total+' credits?','دبلجة '+ids.length+' أسطر محددة بحد أقصى '+quote.max_total+' رصيداً؟');if(!await LisanDialog.confirm(question))return;}
      var job=await api('/api/longdub/'+id+'/corrections/dub','POST',{selected:ids,token:quote.token});state.busy=true;progress(job);await poll();await refreshCredits().catch(function(){$('balance').textContent='…';});
    }catch(ex){error(ex);}finally{if(!state||!state.busy)setBusy(false);}
  };
  $('finish').onclick=async function(){if(working)return;try{if(await LisanDialog.confirm(tr('Release the saved cloned voices? Later re-cloning will cost credits.','تحرير الأصوات المستنسخة المحفوظة؟ تُحتسب إعادة الاستنساخ لاحقاً من الرصيد.'))){await api('/api/longdub/'+project.id+'/corrections/finish','POST',{});notify('success',tr('Cloned voices released. Your exports and reference samples remain saved.','تم تحرير الأصوات المستنسخة. تظل الملفات والعينات المرجعية محفوظة.'));}}catch(ex){error(ex);}};
  $('restore').onclick=async function(){
    var file=$('original').files[0];if(!file||!project||working)return;setBusy(true);
    try{var restored=await api('/api/longdub/'+project.id+'/corrections/restore','POST',{filename:file.name,size:file.size}),chunk=restored.chunk_bytes||8*1024*1024;
      for(var at=0,index=0;at<file.size;at+=chunk,index++){var response=await fetch('/api/longdub/'+restored.id+'/chunk?index='+index,{method:'PUT',body:file.slice(at,at+chunk)});if(!response.ok)throw new Error((await response.json()).error||'Upload failed');var percent=Math.min(100,Math.round(Math.min(at+chunk,file.size)/file.size*100));$('restoreStatus').textContent=tr('Uploading original… ','جارٍ رفع الأصل… ')+percent+'%';progress({percent:percent},tr('Uploading original…','جارٍ رفع الأصل…'));}
      await api('/api/longdub/'+restored.id+'/finish','POST',{});
      var watch=async function(){var job=await api('/api/longdub/'+restored.id);progress(job,tr('Preparing original audio…','جارٍ تحضير الصوت الأصلي…'));if(job.status==='failed')throw new Error(job.error);if(job.status==='editing'){$('restoreStatus').textContent=tr('Original audio recovered.','تمت استعادة الصوت الأصلي.');setBusy(false);state=await api('/api/longdub/'+project.id+'/corrections');renderLines();}else pollTimer=setTimeout(function(){watch().catch(function(ex){error(ex);setBusy(false);});},3000);};await watch();
    }catch(ex){error(ex);setBusy(false);}
  };
  /* Enter man.: the same dialog as the long-dub page (longdub_player.js). This part only says where the sound comes from
     and what happens with the new times. */
  function openPlayer(row) {
    if (working || !project || !window.LisanPlayer || LisanPlayer.isOpen()) return;
    var id = project.id, info = meta[id] || {}, kind = info.has_video ? 'video' : 'audio';
    var who = (state.speaker_list || []).find(function(s) { return s.id === row.speaker_id; });
    LisanPlayer.open({
      lang: lang, index: state.segments.indexOf(row) + 1, speaker: (who && who.name) || row.speaker || '',
      text: row.text || '', arabic: row.arabic_text || '', start: Number(row.start), end: Number(row.end), duration: state.duration,
      size: info.size, kind: kind,
      local: listen && listen.projectId === id ? {url: listen.url, kind: kind} : null,
      fetchSource: function() {
        return api('/api/longdub/' + id + '/corrections/player', 'POST', {}).then(function(source) { return {url: source.url, kind: source.kind}; }, function() { return null; });
      },
      onLocal: function(file, url) { keepFile(id, file, url); },
      onApply: async function(a, b) {
        if (!Number.isFinite(a + b) || a < 0 || b <= a || b > state.duration) throw new Error(tr('Set valid times inside the original duration.', 'أدخل وقتين صالحين ضمن مدة الأصل.'));
        change(row, 'start', a); change(row, 'end', b); change(row, 'manual_time', true);
        await save();
        state.segments.sort(function(x, y) { return x.start - y.start; });
        renderLines();
        return true;
      }
    });
  }
  /* The person's own copy of the film is only played in their browser: nothing is uploaded and nothing costs credits. */
  function keepFile(id, file, url) {
    if (listen && listen.url !== url) URL.revokeObjectURL(listen.url);
    listen = {projectId: id, url: url, name: file.name};
    showPicked();
  }
  function showPicked() {
    var file = $('listenFile').files[0];
    $('pickedFile').textContent = file ? file.name : (listen && listen.projectId === $('projects').value ? listen.name : '');
  }
  function openNote(text) { $('openNote').textContent = text || ''; }
  $('pickFile').onclick = function() { $('listenFile').click(); };
  $('listenFile').onchange = function() { openNote(''); showPicked(); };
  $('openProject').onclick = function() {
    var id = $('projects').value, file = $('listenFile').files[0];
    openNote('');
    if (!id) { openNote(tr('Choose a project first.', 'اختر مشروعاً أولاً.')); return; }
    var info = meta[id] || {};
    if (file && info.size && file.size !== Number(info.size)) { openNote(tr('That isn’t the same file this project was made from.', 'هذا ليس الملف نفسه الذي أُنشئ منه المشروع.')); return; }
    if (file) { if (listen && listen.url) URL.revokeObjectURL(listen.url); listen = {projectId: id, url: URL.createObjectURL(file), name: file.name}; }
    else if (listen && listen.projectId !== id) { URL.revokeObjectURL(listen.url); listen = null; }
    open(id).catch(error);
  };
  $('projects').onchange=function(){openNote('');var f=$('listenFile').files[0],i=meta[this.value]||{};if(f&&i.size&&f.size!==Number(i.size))$('listenFile').value='';showPicked();};
  $('language').onclick=function(){localStorage.setItem('lisan_lang',lang==='en'?'ar':'en');location.reload();};
  $('back').onclick=function(){location.href='/dub-long';};$('appLink').onclick=function(){location.href='/app';};
  $('logout').onclick=function(){try{sessionStorage.removeItem('lisan_notify_log');sessionStorage.removeItem('lisan_notify_log_owner');}catch(e){};fetch('/api/logout',{method:'POST'}).then(function(){location.href='/login';});};
  function dark(on){document.body.classList.toggle('dark',on);localStorage.setItem('lisan_dark_mode',on?'1':'0');$('darkModeBtn').textContent=on?'☀️':'🌙';}
  dark(localStorage.getItem('lisan_dark_mode')!=='0');$('darkModeBtn').onclick=function(){dark(!document.body.classList.contains('dark'));};
  window.addEventListener('beforeunload',function(e){if(dirty){e.preventDefault();e.returnValue='';}});
  staticText();
  api('/api/longdub').then(function(data){$('balance').textContent=data.credits==null?'…':data.credits;$('projects').replaceChildren();var initial=document.createElement('option');initial.value='';initial.textContent=tr('Choose a project…','اختر مشروعاً…');$('projects').appendChild(initial);data.jobs.filter(function(j){return j.status==='done'&&!j.edit_of;}).forEach(function(job){var option=document.createElement('option');option.value=job.id;option.textContent=job.name;meta[job.id]={size:job.size,has_video:!!job.has_video};$('projects').appendChild(option);});}).catch(error);
})();
