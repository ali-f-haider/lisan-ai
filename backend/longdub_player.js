/* The "Enter man." time dialog, shared by the long-dub page and the correction page so both behave identically.
   The caller says where the picture and sound come from and what to do with the new times; everything the person
   sees and presses (play, step, mark, play start to end, volume boost, keys) lives here, once.
   A saved project's film is played from the person's own file in their browser: nothing is uploaded. */
(function () {
  'use strict';

  var S = {
    en: {
      title: function (n, who) { return 'Enter man. - line ' + n + (who ? ' (' + who + ')' : ''); },
      prep: 'Preparing the player... the first time this can take up to a minute.',
      needFile: 'Your video is not on our servers right now (this project is saved). Choose the same file from your computer to use it in this player. It is only played in your browser - nothing is uploaded and nothing costs credits.',
      choose: 'Choose the file', wrongFile: 'That isn’t the same file this project was made from.',
      cantPlay: 'Your browser can’t play this file. Attach the file to the project instead ("Attach the same file") and we will make a playable copy.',
      start: 'Start', end: 'End', setStart: 'Set start here', setEnd: 'Set end here', goTo: 'Go to',
      playRange: 'Play start to end', play: 'Play', pause: 'Pause', speed: 'Speed', boost: 'Volume boost',
      boostHint: 'Raises the volume to help you hear very quiet words.',
      length: function (x) { return 'Line length: ' + x + ' s'; },
      bad: 'The end must be after the start.', saveClose: 'Close and use these times', discard: 'Close without changing',
      keys: 'Keys: Space play/pause · [ set start · ] set end · ← → 0.1 s · Shift+← → 0.01 s · Esc close',
      netErr: 'Could not reach the server. Check your connection and try again.',
      generic: 'Something went wrong. Please try again.'
    },
    ar: {
      title: function (n, who) { return 'إدخال يدوي - السطر ' + n + (who ? ' (' + who + ')' : ''); },
      prep: 'جارٍ تجهيز المشغّل... قد يستغرق هذا حتى دقيقة في المرة الأولى.',
      needFile: 'الفيديو ليس على خوادمنا حالياً (هذا المشروع محفوظ). اختر الملف نفسه من جهازك لاستخدامه في هذا المشغّل. يُشغَّل في متصفحك فقط - لا يُرفع شيء ولا يكلّف أي رصيد.',
      choose: 'اختر الملف', wrongFile: 'هذا ليس الملف نفسه الذي أُنشئ منه المشروع.',
      cantPlay: 'لا يستطيع متصفحك تشغيل هذا الملف. أرفق الملف بالمشروع بدلاً من ذلك («إرفاق الملف نفسه») وسنصنع نسخة قابلة للتشغيل.',
      start: 'البداية', end: 'النهاية', setStart: 'اجعل البداية هنا', setEnd: 'اجعل النهاية هنا', goTo: 'انتقل إلى',
      playRange: 'تشغيل من البداية إلى النهاية', play: 'تشغيل', pause: 'إيقاف مؤقت', speed: 'السرعة', boost: 'تكبير الصوت',
      boostHint: 'يرفع مستوى الصوت لتسمع الكلمات الخافتة جداً.',
      length: function (x) { return 'مدة السطر: ' + x + ' ث'; },
      bad: 'يجب أن تكون النهاية بعد البداية.', saveClose: 'إغلاق واستخدام هذه الأوقات', discard: 'إغلاق دون تغيير',
      keys: 'المفاتيح: المسافة تشغيل/إيقاف · [ البداية · ] النهاية · ← → ‏0.1 ث · Shift+← → ‏0.01 ث · Esc إغلاق',
      netErr: 'تعذّر الاتصال بالخادم. تحقق من الاتصال ثم حاول مرة أخرى.',
      generic: 'حدث خطأ. حاول مرة أخرى.'
    }
  };

  var current = null;   // only one box at a time

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; });
  }
  // seconds -> "m:ss.mmm"
  function fmt(sec) {
    var ms = Math.round(Math.max(0, +sec || 0) * 1000), m = Math.floor(ms / 60000), x = (ms - m * 60000) / 1000;
    return m + ':' + (x < 10 ? '0' : '') + x.toFixed(3);
  }
  // "1:23.5", "83.5" or Arabic digits -> seconds, or null when it isn't a time
  function parse(str) {
    var t = String(str || '').trim().replace(/[٠-٩]/g, function (d) { return String(d.charCodeAt(0) - 1632); })
      .replace(/[۰-۹]/g, function (d) { return String(d.charCodeAt(0) - 1776); }).replace(/[,٫،]/g, '.');
    var m = t.match(/^(?:(\d+):)?(\d+(?:\.\d+)?)$/);
    if (!m) return null;
    return (m[1] ? parseInt(m[1], 10) * 60 : 0) + parseFloat(m[2]);
  }

  function open(opts) {
    if (current) return null;
    opts = opts || {};
    var T = S[opts.lang === 'ar' ? 'ar' : 'en'];
    var st = { opts: opts, T: T, a: +opts.start || 0, b: +opts.end || 0, raf: 0, stopAt: null, ctx: null, gain: null, el: null, closing: false, dur: +opts.duration || 0 };
    st.a0 = st.a; st.b0 = st.b;
    current = st;

    var ov = document.createElement('div'); ov.className = 'ld-pl-overlay';
    var box = document.createElement('div'); box.className = 'ld-pl'; if (opts.lang === 'ar') box.dir = 'rtl';
    box.innerHTML =
      '<h4>' + esc(T.title(opts.index, opts.speaker || '')) + '</h4>' +
      '<div class="ld-pl-info" id="plEnglish" dir="ltr"></div>' +
      '<div id="plPrep" class="ld-pl-info">' + esc(T.prep) + '</div>' +
      '<div id="plNeed" class="hidden"></div>' +
      '<div id="plMain" class="hidden">' +
        '<video id="plVideo" preload="auto" playsinline></video>' +
        '<div class="ld-pl-time"><span id="plNow">0:00.000</span> <small>/ <span id="plTotal"></span></small></div>' +
        '<input type="range" id="plSeek" min="0" max="1" step="0.001" value="0">' +
        '<div class="ld-pl-row" dir="ltr">' +
          '<button type="button" class="btn-plain" data-step="-1">−1 s</button><button type="button" class="btn-plain" data-step="-0.1">−0.1</button><button type="button" class="btn-plain" data-step="-0.01">−0.01</button>' +
          '<button type="button" id="plPlayBtn">' + esc(T.play) + '</button>' +
          '<button type="button" class="btn-plain" data-step="0.01">+0.01</button><button type="button" class="btn-plain" data-step="0.1">+0.1</button><button type="button" class="btn-plain" data-step="1">+1 s</button>' +
        '</div>' +
        '<div class="ld-pl-row">' +
          '<label>' + esc(T.speed) + ' <select id="plSpeed"><option value="0.25">0.25×</option><option value="0.5">0.5×</option><option value="0.75">0.75×</option><option value="1" selected>1×</option></select></label>' +
          '<label title="' + esc(T.boostHint) + '">' + esc(T.boost) + ' <select id="plBoost"><option value="1">1×</option><option value="2">2×</option><option value="4">4×</option><option value="8">8×</option></select></label>' +
        '</div>' +
        '<div class="ld-pl-marks">' +
          '<div class="ld-pl-mark"><b>' + esc(T.start) + '</b><input type="text" id="plA" inputmode="decimal" autocomplete="off"><div class="btns"><button type="button" id="plSetA">' + esc(T.setStart) + '</button><button type="button" class="btn-plain" id="plGoA">' + esc(T.goTo) + '</button></div></div>' +
          '<div class="ld-pl-mark"><b>' + esc(T.end) + '</b><input type="text" id="plB" inputmode="decimal" autocomplete="off"><div class="btns"><button type="button" id="plSetB">' + esc(T.setEnd) + '</button><button type="button" class="btn-plain" id="plGoB">' + esc(T.goTo) + '</button></div></div>' +
        '</div>' +
        '<div class="ld-pl-row"><button type="button" class="btn-plain" id="plRange">' + esc(T.playRange) + '</button></div>' +
        '<div class="ld-pl-info" id="plLen"></div>' +
        '<div class="ld-pl-keys">' + esc(T.keys) + '</div>' +
      '</div>' +
      '<div class="ld-pl-err hidden" id="plErr"></div>' +
      '<div class="ld-pl-foot"><button type="button" class="btn-plain" id="plDiscard">' + esc(T.discard) + '</button><button type="button" class="green" id="plClose">' + esc(T.saveClose) + '</button></div>';
    ov.appendChild(box); document.body.appendChild(ov);
    st.ov = ov;
    var q = function (id) { return box.querySelector('#' + id); };
    st.q = q;
    q('plEnglish').textContent = (opts.text || '') + (opts.text && opts.arabic ? '  ·  ' : '') + (opts.arabic || '');

    st.err = function (msg) { var e = q('plErr'); e.textContent = msg || ''; e.classList.toggle('hidden', !msg); };
    q('plDiscard').onclick = function () { closeOnly(st); };
    q('plClose').onclick = function () { applyAndClose(st); };

    st.onKey = function (e) {
      if (current !== st) return;
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); applyAndClose(st); return; }
      if (!st.el) return;
      var tag = e.target && e.target.tagName;
      if ((tag === 'INPUT' && e.target.type === 'text') || tag === 'SELECT' || tag === 'TEXTAREA') return;
      if (e.key === ' ') { if (tag === 'BUTTON') return; e.preventDefault(); toggle(st); }
      else if (e.key === '[') { e.preventDefault(); setA(st); }
      else if (e.key === ']') { e.preventDefault(); setB(st); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); step(st, e.shiftKey ? -0.01 : -0.1); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); step(st, e.shiftKey ? 0.01 : 0.1); }
    };
    document.addEventListener('keydown', st.onKey, true);

    getSource(st).then(function (src) {
      if (current !== st || !src) return;
      startVideo(st, src);
    });
    return st;
  }

  // Where the picture and sound come from: the person's own file if they chose one, else the caller's server copy,
  // else ask for the file.
  function getSource(st) {
    var o = st.opts, T = st.T, q = st.q;
    if (o.local && o.local.url) return Promise.resolve({ url: o.local.url, kind: o.local.kind || o.kind || 'video' });
    var ask = o.fetchSource ? Promise.resolve().then(function () { return o.fetchSource(); }) : Promise.resolve(null);
    return ask.then(function (r) {
      if (current !== st) return null;
      if (r && r.url) return r;
      if (r && r.error) { q('plPrep').classList.add('hidden'); st.err(r.error); return null; }
      return askForFile(st);
    }, function () { if (current === st) { q('plPrep').classList.add('hidden'); st.err(T.netErr); } return null; });
  }

  function askForFile(st) {
    var o = st.opts, T = st.T, q = st.q;
    q('plPrep').classList.add('hidden');
    var need = q('plNeed'); need.classList.remove('hidden');
    need.innerHTML = '<p class="ld-warn">' + esc(T.needFile) + '</p><div class="ld-row-btns" style="justify-content:center;"><button type="button" id="plPick">' + esc(T.choose) + '</button></div>' +
      '<input type="file" id="plFile" accept="video/*,audio/*,.mkv,.mov,.mp4,.webm,.avi,.mp3,.wav,.m4a,.aac,.flac,.ogg" class="hidden">';
    return new Promise(function (resolve) {
      var fi = need.querySelector('#plFile');
      need.querySelector('#plPick').onclick = function () { fi.click(); };
      fi.onchange = function () {
        var f = fi.files && fi.files[0]; if (!f) return;
        if (o.size && f.size !== +o.size) { st.err(T.wrongFile); return; }
        st.err('');
        var url = URL.createObjectURL(f);
        if (o.onLocal) { try { o.onLocal(f, url); } catch (e) { /* the caller's note-keeping must not stop playback */ } }
        need.classList.add('hidden');
        resolve({ url: url, kind: o.kind || 'video' });
      };
    }).then(function (src) { if (current === st) q('plPrep').classList.remove('hidden'); return src; });
  }

  function startVideo(st, src) {
    var q = st.q, T = st.T, el = q('plVideo');
    st.el = el;
    if (src.kind === 'audio') el.classList.add('audio');
    el.src = src.url;
    el.onerror = function () { q('plPrep').classList.add('hidden'); q('plMain').classList.add('hidden'); st.err(T.cantPlay); };
    el.addEventListener('loadedmetadata', function () {
      q('plPrep').classList.add('hidden'); q('plMain').classList.remove('hidden');
      st.dur = el.duration || st.dur || 0;
      q('plTotal').textContent = fmt(st.dur);
      q('plSeek').max = st.dur;
      q('plA').value = fmt(st.a); q('plB').value = fmt(st.b);
      el.currentTime = Math.max(0, st.a);   // open exactly on the line's start time
      refresh(st);
    });
    el.addEventListener('timeupdate', function () { checkStop(st); refresh(st); });
    el.addEventListener('seeked', function () { refresh(st); });
    el.addEventListener('play', function () { q('plPlayBtn').textContent = T.pause; loop(st); });
    el.addEventListener('pause', function () { q('plPlayBtn').textContent = T.play; refresh(st); });
    el.addEventListener('ended', function () { st.stopAt = null; q('plPlayBtn').textContent = T.play; });
    q('plPlayBtn').onclick = function () { toggle(st); };
    q('plSeek').oninput = function () { st.stopAt = null; el.currentTime = parseFloat(this.value); refresh(st); };
    Array.prototype.forEach.call(st.ov.querySelectorAll('[data-step]'), function (b) { b.onclick = function () { step(st, parseFloat(b.getAttribute('data-step'))); }; });
    q('plSpeed').onchange = function () { el.playbackRate = parseFloat(this.value); };
    q('plBoost').onchange = function () { boost(st, parseFloat(this.value)); };
    q('plSetA').onclick = function () { setA(st); };
    q('plSetB').onclick = function () { setB(st); };
    q('plGoA').onclick = function () { st.stopAt = null; el.currentTime = st.a; };
    q('plGoB').onclick = function () { st.stopAt = null; el.currentTime = st.b; };
    q('plRange').onclick = function () { playRange(st); };
    ['plA', 'plB'].forEach(function (id) {
      q(id).onchange = function () {
        var v = parse(this.value);
        if (v === null) { this.value = fmt(id === 'plA' ? st.a : st.b); return; }
        v = Math.round(v * 1000) / 1000;
        if (id === 'plA') st.a = v; else st.b = v;
        this.value = fmt(v); refresh(st);
      };
    });
  }

  // Play from the start mark and pause by itself at the end mark.
  function playRange(st) {
    var el = st.el, q = st.q;
    if (!el) return;
    if (!(st.b > st.a)) { st.err(st.T.bad); return; }
    st.err('');
    st.stopAt = null;
    boostInit(st);
    var a = st.a, b = st.b, started = false;
    function go() {
      if (started || current !== st) return;
      started = true;
      st.stopAt = b;
      var p = el.play(); if (p && p.catch) p.catch(function () { st.stopAt = null; });
    }
    if (Math.abs((el.currentTime || 0) - a) < 0.005 && !el.seeking) { go(); return; }
    // Wait until the jump to the start has really happened; otherwise the old position (maybe past the end mark)
    // would be read as "already at the end" and playback would stop at once.
    var done = function () { el.removeEventListener('seeked', done); go(); };
    el.addEventListener('seeked', done);
    el.currentTime = a;
    setTimeout(function () { el.removeEventListener('seeked', done); go(); }, 600);
  }
  // Called by both the animation loop and the media clock, so a hidden tab (no animation frames) still stops on time.
  function checkStop(st) {
    var el = st.el;
    if (!el || st.stopAt === null || el.seeking) return false;
    if (el.currentTime >= st.stopAt) {
      var at = st.stopAt;
      st.stopAt = null;
      el.pause();
      try { el.currentTime = at; } catch (e) { /* a pause at the end mark is enough */ }
      refresh(st);
      return true;
    }
    return false;
  }

  function nowT(st) { return Math.round((st.el.currentTime || 0) * 1000) / 1000; }
  function refresh(st) {
    if (!st.el) return;
    var t = nowT(st), q = st.q;
    q('plNow').textContent = fmt(t);
    var sk = q('plSeek'); if (document.activeElement !== sk) sk.value = t;
    var len = st.b - st.a;
    q('plLen').textContent = len > 0 ? st.T.length(len.toFixed(3)) : st.T.bad;
  }
  function loop(st) {
    if (st.raf) cancelAnimationFrame(st.raf);
    var tick = function () {
      if (current !== st || !st.el || st.el.paused) { st.raf = 0; return; }
      if (checkStop(st)) { st.raf = 0; return; }
      refresh(st); st.raf = requestAnimationFrame(tick);
    };
    st.raf = requestAnimationFrame(tick);
  }
  function toggle(st) {
    if (!st.el) return;
    st.stopAt = null;
    boostInit(st);
    if (st.el.paused) { var p = st.el.play(); if (p && p.catch) p.catch(function () {}); } else st.el.pause();
  }
  function step(st, d) {
    if (!st.el) return;
    st.stopAt = null;
    st.el.pause();
    st.el.currentTime = Math.min(Math.max(0, st.el.currentTime + d), st.dur || 1e9);
    refresh(st);
  }
  function setA(st) { if (!st.el) return; st.a = nowT(st); st.q('plA').value = fmt(st.a); st.err(''); refresh(st); }
  function setB(st) { if (!st.el) return; st.b = nowT(st); st.q('plB').value = fmt(st.b); st.err(''); refresh(st); }
  function boostInit(st) {
    if (st.ctx || !(window.AudioContext || window.webkitAudioContext)) return;
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      st.ctx = new AC();
      var srcNode = st.ctx.createMediaElementSource(st.el);
      st.gain = st.ctx.createGain(); st.gain.gain.value = parseFloat(st.q('plBoost').value) || 1;
      var comp = st.ctx.createDynamicsCompressor(); comp.threshold.value = -6; comp.ratio.value = 12;
      srcNode.connect(st.gain); st.gain.connect(comp); comp.connect(st.ctx.destination);
    } catch (e) { st.ctx = null; st.gain = null; }
  }
  function boost(st, v) {
    boostInit(st);
    if (st.gain) st.gain.gain.value = v;
    try { if (st.ctx && st.ctx.state === 'suspended') st.ctx.resume(); } catch (e) { /* the picture still plays */ }
  }

  function closeOnly(st) {
    if (current !== st) return;
    if (st.raf) cancelAnimationFrame(st.raf);
    try { if (st.el) { st.el.pause(); st.el.removeAttribute('src'); st.el.load(); } } catch (e) { /* closing anyway */ }
    try { if (st.ctx) st.ctx.close(); } catch (e) { /* closing anyway */ }
    document.removeEventListener('keydown', st.onKey, true);
    if (st.ov.parentNode) st.ov.parentNode.removeChild(st.ov);
    current = null;
  }

  // Closing puts the times into the line, but only when they changed. The caller's onApply may refuse (Error) and the box stays open.
  function applyAndClose(st) {
    if (st.closing) return;
    var T = st.T, changed = Math.abs(st.a - st.a0) > 0.0004 || Math.abs(st.b - st.b0) > 0.0004;
    if (!changed) { closeOnly(st); return; }
    if (!(st.b > st.a)) { st.err(T.bad); st.q('plMain').classList.remove('hidden'); return; }
    if (!st.opts.onApply) { closeOnly(st); return; }
    st.closing = true;
    var btn = st.q('plClose'); btn.disabled = true;
    var r;
    try { r = st.opts.onApply(st.a, st.b); } catch (e) { r = Promise.reject(e); }
    Promise.resolve(r).then(function (ok) {
      st.closing = false; btn.disabled = false;
      if (ok === false) return;               // the caller was busy: stay open, nothing changed
      closeOnly(st);
    }, function (e) {
      st.closing = false; btn.disabled = false;
      st.err((e && e.message) || T.generic);
    });
  }

  window.LisanPlayer = { open: open, isOpen: function () { return !!current; }, fmt: fmt, parse: parse, _state: function () { return current; } };
})();
