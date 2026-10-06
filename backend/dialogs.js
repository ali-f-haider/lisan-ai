/* Lisan AI custom dialogs -- replaces the browser's own alert() / confirm() /
   prompt() message boxes everywhere in the app (main app, Account page,
   Pricing page, Admin panel), styled like the Buy Credits box.

   API (all return Promises, so callers `await` them or use .then):
     LisanDialog.alert(message, {title, okText, type})            -> Promise<void>
     LisanDialog.confirm(message, {title, okText, cancelText, danger}) -> Promise<boolean>
     LisanDialog.prompt(message, {title, value, placeholder, okText, cancelText, required})
                                                                   -> Promise<string|null>
     LisanDialog.trim(file, {duration, maxSec, minSec, reason, sizeMB, capMB})
                                                                   -> Promise<{start,end}|null>
       ("choose the part to dub" box for a video/audio that is longer -- or
       bigger -- than the upload limit; seconds in, seconds out)
   type: "error" | "success" | "warning" (only tints the title).
   danger: true makes the OK button red and puts the initial focus on Cancel.

   Colors come from the page's own CSS variables (--card, --text, --border,
   --primary, ...) so the box follows Classic/New style and light/dark mode
   automatically; every color also has a --ld-* override (used by the Admin
   page, which has its own dark palette) and a plain fallback (used by pages
   that define none of these variables). Messages are always inserted as
   plain text, never HTML. Several dialogs at once are shown one after the
   other. Esc = cancel, clicking the dark backdrop = cancel.
   Arabic buttons/titles are used when the page language is Arabic. */
(function () {
    if (window.LisanDialog) return;

    var CSS = [
        ".ld-overlay{position:fixed;inset:0;background:rgba(15,23,42,0.55);z-index:100000;display:flex;align-items:center;justify-content:center;padding:20px;font-family:inherit;}",
        ".ld-box{background:var(--ld-card,var(--card,#ffffff));color:var(--ld-text,var(--text,#1a1a2e));border:1px solid var(--ld-border,var(--border,#e5e7eb));border-radius:16px;padding:24px;width:380px;max-width:92vw;max-height:90vh;overflow-y:auto;box-shadow:var(--ld-shadow,var(--shadow-lg,0 20px 60px rgba(0,0,0,0.3)));box-sizing:border-box;}",
        ".ld-title{margin:0 0 10px;font-size:17px;font-weight:700;color:var(--ld-title,var(--primary-fg,#1a237e));}",
        ".ld-title.ld-error{color:var(--ld-danger,var(--error,#dc2626));}",
        ".ld-title.ld-success{color:var(--ld-success,var(--success,#059669));}",
        ".ld-title.ld-warning{color:var(--ld-warning,var(--warning,#d97706));}",
        ".ld-msg{margin:0 0 18px;font-size:14px;line-height:1.5;white-space:pre-line;overflow-wrap:anywhere;color:var(--ld-text,var(--text,#1a1a2e));}",
        "input.ld-input{display:block;width:100%;box-sizing:border-box;margin:0 0 6px;padding:10px 12px;font-size:14px;font-family:inherit;border-radius:10px;border:1px solid var(--ld-border,var(--border,#e5e7eb));background:var(--ld-input-bg,var(--input-bg,#ffffff));color:var(--ld-text,var(--text,#1a1a2e));}",
        "input.ld-input:focus{outline:2px solid var(--ld-primary,var(--primary,#1a237e));outline-offset:0;}",
        ".ld-err{min-height:16px;margin:0 0 12px;font-size:12px;color:var(--ld-danger,var(--error,#dc2626));}",
        ".ld-btns{display:flex;gap:10px;justify-content:flex-end;}",
        ".ld-btn{flex:1;display:inline-flex;align-items:center;justify-content:center;padding:10px 14px;border-radius:10px;font-size:14px;font-weight:600;font-family:inherit;cursor:pointer;margin:0;transform:none;border:1px solid var(--ld-border,var(--border,#e5e7eb));background:var(--ld-btn-bg,var(--logout-bg,#f3f4f6));color:var(--ld-btn-fg,var(--text-muted,#6b7280));}",
        ".ld-btn:hover{background:var(--ld-btn-hover,var(--logout-hover-bg,#e5e7eb));transform:none;}",
        ".ld-btn.ld-ok{background:var(--ld-primary,var(--primary,#1a237e));border-color:var(--ld-primary,var(--primary,#1a237e));color:var(--ld-primary-fg,#ffffff);}",
        ".ld-btn.ld-ok:hover{background:var(--ld-primary-hover,var(--primary-light,#3949ab));border-color:var(--ld-primary-hover,var(--primary-light,#3949ab));}",
        ".ld-btn.ld-danger{background:var(--ld-danger,var(--error,#dc2626));border-color:var(--ld-danger,var(--error,#dc2626));color:#ffffff;}",
        ".ld-btn.ld-danger:hover{filter:brightness(0.92);background:var(--ld-danger,var(--error,#dc2626));}",
        ".ld-btn:focus-visible{outline:2px solid var(--ld-primary,var(--primary,#1a237e));outline-offset:2px;}",
        ".ld-box.ld-wide{width:460px;}",
        "video.ld-video{display:block;width:100%;max-width:100%;max-height:230px;margin:0 auto 12px;border-radius:10px;background:#000;}",
        ".ld-row{display:flex;align-items:center;gap:10px;margin:0 0 10px;}",
        ".ld-row .ld-lbl{min-width:58px;font-size:13px;font-weight:600;color:var(--ld-text,var(--text,#1a1a2e));}",
        "input.ld-range{flex:1;width:auto;min-width:0;margin:0;padding:0;border:none;background:none;box-shadow:none;accent-color:var(--ld-primary,var(--primary,#1a237e));}",
        "input.ld-time{display:block;width:92px;flex:none;margin:0;text-align:center;}",
        ".ld-sel{margin:0 0 10px;font-size:13px;font-weight:600;text-align:center;color:var(--ld-title,var(--primary-fg,#1a237e));}",
        ".ld-mini{display:flex;gap:8px;margin:0 0 12px;}",
        ".ld-mini .ld-btn{padding:7px 10px;font-size:13px;}",
        ".ld-opt{display:flex;gap:10px;align-items:flex-start;border:1px solid var(--ld-border,var(--border,#e5e7eb));border-radius:10px;padding:10px 12px;margin:0 0 8px;cursor:pointer;font-size:13px;font-weight:400;line-height:1.45;}",
        ".ld-opt.ld-on{border-color:var(--ld-primary,var(--primary,#1a237e));}",
        ".ld-opt input{margin:3px 0 0;flex:none;width:auto;accent-color:var(--ld-primary,var(--primary,#1a237e));}",
        ".ld-opt b{display:block;font-size:13.5px;font-weight:700;}",
        ".ld-opt i{display:block;font-style:normal;font-weight:400;opacity:.8;}",
        ".ld-fname{font-weight:600;word-break:break-all;margin:0 0 8px;font-size:13px;}",
        ".ld-chk{display:flex;gap:8px;align-items:flex-start;font-size:13px;font-weight:400;line-height:1.45;margin:4px 0 14px;cursor:pointer;}",
        ".ld-chk input{margin:3px 0 0;flex:none;width:auto;}",
        ".ld-rtl{direction:rtl;text-align:right;}",
        ".ld-rtl .ld-btns{flex-direction:row-reverse;}"
    ].join("\n");

    var TEXT = {
        en: { ok: "OK", cancel: "Cancel", alertTitle: "Notice", confirmTitle: "Please confirm", promptTitle: "Enter a value", required: "This field is required.",
              trimTitle: "Choose the part to dub",
              trimLong: "This file is {dur} long. Up to {max} seconds can be dubbed at a time, so choose the part you want.",
              trimSize: "This file is {mb} MB, over the {cap} MB upload limit. Choose the part to use: only that section is kept and compressed.",
              trimBlind: "This file can't be previewed in your browser. Type where the part you want starts (for example 2:15) and how long it should be (up to {max} seconds).",
              trimStart: "Start", trimLength: "Length", trimHere: "Start at current position", trimPreview: "Preview section",
              trimSelected: "Selected: {a} to {b} ({n} s)", trimUse: "Use this section",
              trimNoPreview: "Preview isn't available for this file type, but you can still choose the section.",
              trimBadStart: "Enter the start as a time such as 2:15 (minutes:seconds).",
              trimBadLen: "The length must be between {min} and {max} seconds." },
        ar: { ok: "موافق", cancel: "إلغاء", alertTitle: "تنبيه", confirmTitle: "يرجى التأكيد", promptTitle: "أدخل قيمة", required: "هذا الحقل مطلوب.",
              trimTitle: "اختر الجزء المراد دبلجته",
              trimLong: "مدة هذا الملف {dur}. يمكن دبلجة حتى {max} ثانية في المرة الواحدة، فاختر الجزء الذي تريده.",
              trimSize: "حجم هذا الملف {mb} ميغابايت، وهو أكبر من حد الرفع ({cap} ميغابايت). اختر الجزء المطلوب: سيتم الاحتفاظ بهذا المقطع فقط وضغطه.",
              trimBlind: "لا يمكن معاينة هذا الملف في المتصفح. اكتب وقت بداية الجزء الذي تريده (مثل 2:15) ومدته (حتى {max} ثانية).",
              trimStart: "البداية", trimLength: "المدة", trimHere: "ابدأ من الموضع الحالي", trimPreview: "معاينة المقطع",
              trimSelected: "المحدد: من {a} إلى {b} ({n} ث)", trimUse: "استخدم هذا الجزء",
              trimNoPreview: "المعاينة غير متاحة لهذا النوع من الملفات، لكن يمكنك اختيار المقطع.",
              trimBadStart: "أدخل البداية كوقت مثل 2:15 (دقائق:ثواني).",
              trimBadLen: "يجب أن تكون المدة بين {min} و{max} ثانية." }
    };

    function isArabic() {
        try {
            if (window.currentLang) return window.currentLang === "ar";
            if (document.body && document.body.classList.contains("lang-ar")) return true;
            if (document.documentElement.lang === "ar") return true;
            return (localStorage.getItem("lisan_lang") || "") === "ar";
        } catch (e) { return false; }
    }

    function injectStyle() {
        if (document.getElementById("lisanDialogStyle")) return;
        var st = document.createElement("style");
        st.id = "lisanDialogStyle";
        st.textContent = CSS;
        (document.head || document.documentElement).appendChild(st);
    }

    var queue = Promise.resolve();
    function enqueue(fn) {
        var p = queue.then(fn);
        queue = p.then(function () {}, function () {});
        return p;
    }

    function show(kind, message, opts) {
        opts = opts || {};
        return new Promise(function (resolve) {
            injectStyle();
            var T = TEXT[isArabic() ? "ar" : "en"];
            var previouslyFocused = document.activeElement;

            var overlay = document.createElement("div");
            overlay.className = "ld-overlay";
            var box = document.createElement("div");
            box.className = "ld-box" + (isArabic() ? " ld-rtl" : "");
            box.setAttribute("role", kind === "alert" ? "alertdialog" : "dialog");
            box.setAttribute("aria-modal", "true");

            var title = document.createElement("h3");
            title.className = "ld-title" + (opts.type ? " ld-" + opts.type : "");
            title.textContent = opts.title || T[kind + "Title"];
            box.appendChild(title);

            var msg = document.createElement("p");
            msg.className = "ld-msg";
            msg.textContent = message == null ? "" : String(message);
            box.appendChild(msg);

            var input = null, errEl = null;
            if (kind === "prompt") {
                input = document.createElement("input");
                input.type = "text";
                input.className = "ld-input";
                input.value = opts.value == null ? "" : String(opts.value);
                if (opts.placeholder) input.placeholder = opts.placeholder;
                box.appendChild(input);
                errEl = document.createElement("p");
                errEl.className = "ld-err";
                box.appendChild(errEl);
            }

            var btns = document.createElement("div");
            btns.className = "ld-btns";
            var cancelBtn = null;
            if (kind !== "alert") {
                cancelBtn = document.createElement("button");
                cancelBtn.type = "button";
                cancelBtn.className = "ld-btn ld-cancel";
                cancelBtn.textContent = opts.cancelText || T.cancel;
                btns.appendChild(cancelBtn);
            }
            var okBtn = document.createElement("button");
            okBtn.type = "button";
            okBtn.className = "ld-btn ld-ok" + (opts.danger ? " ld-danger" : "");
            okBtn.textContent = opts.okText || T.ok;
            btns.appendChild(okBtn);
            box.appendChild(btns);
            overlay.appendChild(box);
            document.body.appendChild(overlay);

            var done = false;
            function close(result) {
                if (done) return;
                done = true;
                document.removeEventListener("keydown", onKey, true);
                if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
                try { if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus(); } catch (e) {}
                resolve(result);
            }
            function accept() {
                if (kind === "prompt") {
                    var v = input.value;
                    if (opts.required && !v.trim()) { errEl.textContent = T.required; input.focus(); return; }
                    close(v);
                } else if (kind === "confirm") {
                    close(true);
                } else {
                    close(undefined);
                }
            }
            function dismiss() {
                close(kind === "prompt" ? null : (kind === "confirm" ? false : undefined));
            }
            function onKey(e) {
                if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); dismiss(); return; }
                if (e.key === "Enter" && kind === "prompt" && e.target === input) { e.preventDefault(); accept(); return; }
                if (e.key === "Tab") {
                    var f = box.querySelectorAll("input,button");
                    if (!f.length) return;
                    var first = f[0], last = f[f.length - 1];
                    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
                    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
                    else if (!box.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
                }
            }
            okBtn.onclick = accept;
            if (cancelBtn) cancelBtn.onclick = dismiss;
            overlay.addEventListener("mousedown", function (e) { if (e.target === overlay) dismiss(); });
            document.addEventListener("keydown", onKey, true);

            setTimeout(function () {
                try {
                    if (input) { input.focus(); input.select(); }
                    else if (opts.danger && cancelBtn) cancelBtn.focus();
                    else okBtn.focus();
                } catch (e) {}
            }, 0);
        });
    }

    // ---- "Choose the part to dub" box (long / large uploads) ----
    function pad2(n) { return (n < 10 ? "0" : "") + n; }
    function fmtTime(s) {
        var t = Math.round(Math.max(0, +s || 0) * 10);       // tenths of a second
        var h = Math.floor(t / 36000), m = Math.floor((t % 36000) / 600), sr = (t % 600) / 10;
        var secStr = (sr < 10 ? "0" : "") + (Math.round(sr) === sr ? String(sr) : sr.toFixed(1));
        return h ? (h + ":" + pad2(m) + ":" + secStr) : (m + ":" + secStr);
    }
    // "83", "83.5", "1:23", "1:23.5", "1:02:03" -> seconds, or NaN
    function parseTime(str) {
        str = String(str == null ? "" : str).trim();
        if (!str) return NaN;
        var parts = str.split(":");
        if (parts.length > 3) return NaN;
        var total = 0;
        for (var i = 0; i < parts.length; i++) {
            if (!/^\d+(\.\d+)?$/.test(parts[i].trim())) return NaN;
            total = total * 60 + parseFloat(parts[i]);
        }
        return total;
    }
    function round1(x) { return Math.round(x * 10) / 10; }
    function round3(x) { return Math.round(x * 1000) / 1000; }
    function clamp(x, lo, hi) { return Math.min(hi, Math.max(lo, x)); }

    function showTrim(file, opts) {
        opts = opts || {};
        return new Promise(function (resolve) {
            injectStyle();
            var T = TEXT[isArabic() ? "ar" : "en"];
            var previouslyFocused = document.activeElement;
            var maxSec = opts.maxSec || 30, minSec = opts.minSec || 4;
            var dur = (typeof opts.duration === "number" && isFinite(opts.duration) && opts.duration > 0) ? opts.duration : null;
            var lenMax = dur !== null ? Math.min(maxSec, dur) : maxSec;
            var lenMin = Math.min(minSec, lenMax);
            var state = { start: 0, len: lenMax };

            var overlay = document.createElement("div");
            overlay.className = "ld-overlay";
            var box = document.createElement("div");
            box.className = "ld-box ld-wide" + (isArabic() ? " ld-rtl" : "");
            box.setAttribute("role", "dialog");
            box.setAttribute("aria-modal", "true");

            var title = document.createElement("h3");
            title.className = "ld-title";
            title.textContent = T.trimTitle;
            box.appendChild(title);

            var msg = document.createElement("p");
            msg.className = "ld-msg";
            var msgText;
            if (dur === null) msgText = T.trimBlind;
            else if (opts.reason === "size") msgText = T.trimSize;
            else msgText = T.trimLong;
            msg.textContent = msgText.replace("{dur}", fmtTime(dur || 0)).replace("{max}", String(maxSec))
                .replace("{mb}", String(Math.round(opts.sizeMB || 0))).replace("{cap}", String(opts.capMB || 50));
            box.appendChild(msg);

            var objUrl = null, video = null, stopAt = null;
            var mini = null;
            if (dur !== null) {
                try {
                    objUrl = URL.createObjectURL(file);
                    video = document.createElement("video");
                    video.className = "ld-video";
                    video.controls = true;
                    video.preload = "metadata";
                    video.setAttribute("playsinline", "");
                    video.src = objUrl;
                    box.appendChild(video);
                } catch (e) { video = null; }
            }

            function makeRow(label, withRange) {
                var row = document.createElement("div");
                row.className = "ld-row";
                var lbl = document.createElement("span");
                lbl.className = "ld-lbl";
                lbl.textContent = label;
                row.appendChild(lbl);
                var range = null;
                if (withRange) {
                    range = document.createElement("input");
                    range.type = "range";
                    range.className = "ld-range";
                    range.step = "0.1";
                    row.appendChild(range);
                }
                var txt = document.createElement("input");
                txt.type = "text";
                txt.className = "ld-input ld-time";
                txt.setAttribute("inputmode", "decimal");
                txt.setAttribute("autocomplete", "off");
                row.appendChild(txt);
                box.appendChild(row);
                return { range: range, txt: txt };
            }
            var startRow = makeRow(T.trimStart, dur !== null);
            var lenRow = makeRow(T.trimLength, dur !== null && lenMax > lenMin);
            if (lenRow.range) { lenRow.range.min = String(lenMin); lenRow.range.max = String(lenMax); }

            var sel = document.createElement("p");
            sel.className = "ld-sel";
            box.appendChild(sel);

            var noPreview = document.createElement("p");
            noPreview.className = "ld-msg";
            noPreview.style.fontSize = "12px";
            noPreview.style.display = "none";
            noPreview.textContent = T.trimNoPreview;
            box.appendChild(noPreview);

            var hereBtn = null, previewBtn = null;
            if (video) {
                mini = document.createElement("div");
                mini.className = "ld-mini";
                hereBtn = document.createElement("button");
                hereBtn.type = "button"; hereBtn.className = "ld-btn"; hereBtn.textContent = T.trimHere;
                previewBtn = document.createElement("button");
                previewBtn.type = "button"; previewBtn.className = "ld-btn"; previewBtn.textContent = T.trimPreview;
                mini.appendChild(hereBtn); mini.appendChild(previewBtn);
                box.appendChild(mini);
            }

            var errEl = document.createElement("p");
            errEl.className = "ld-err";
            box.appendChild(errEl);

            var btns = document.createElement("div");
            btns.className = "ld-btns";
            var cancelBtn = document.createElement("button");
            cancelBtn.type = "button"; cancelBtn.className = "ld-btn ld-cancel"; cancelBtn.textContent = T.cancel;
            var okBtn = document.createElement("button");
            okBtn.type = "button"; okBtn.className = "ld-btn ld-ok"; okBtn.textContent = T.trimUse;
            btns.appendChild(cancelBtn); btns.appendChild(okBtn);
            box.appendChild(btns);
            overlay.appendChild(box);
            document.body.appendChild(overlay);

            // Push state into every control. `skip` = the text field the user
            // is typing in right now (don't rewrite it under their fingers).
            function render(skip) {
                state.len = clamp(state.len, lenMin, lenMax);
                var startMax = dur !== null ? Math.max(0, dur - state.len) : 1e9;
                state.start = clamp(state.start, 0, startMax);
                if (startRow.range) { startRow.range.max = String(startMax); startRow.range.value = String(state.start); }
                if (lenRow.range) lenRow.range.value = String(state.len);
                if (skip !== "start") startRow.txt.value = fmtTime(state.start);
                if (skip !== "len") lenRow.txt.value = String(round1(state.len));
                sel.textContent = T.trimSelected.replace("{a}", fmtTime(state.start))
                    .replace("{b}", fmtTime(state.start + state.len)).replace("{n}", String(round1(state.len)));
            }
            render();

            if (startRow.range) startRow.range.addEventListener("input", function () { state.start = parseFloat(startRow.range.value) || 0; errEl.textContent = ""; render(); });
            if (lenRow.range) lenRow.range.addEventListener("input", function () { state.len = parseFloat(lenRow.range.value) || lenMax; errEl.textContent = ""; render(); });
            startRow.txt.addEventListener("input", function () {
                var v = parseTime(startRow.txt.value);
                if (!isNaN(v)) { state.start = v; errEl.textContent = ""; render("start"); }
            });
            startRow.txt.addEventListener("change", function () { render(); });
            lenRow.txt.addEventListener("input", function () {
                var v = parseTime(lenRow.txt.value);
                if (!isNaN(v)) { state.len = v; errEl.textContent = ""; render("len"); }
            });
            lenRow.txt.addEventListener("change", function () { render(); });

            if (video) {
                video.addEventListener("timeupdate", function () {
                    if (stopAt !== null && video.currentTime >= stopAt) { stopAt = null; video.pause(); }
                });
                video.addEventListener("pause", function () { stopAt = null; });
                video.addEventListener("error", function () {
                    // Browser can't decode it (e.g. HEVC .mov, .mkv): drop the
                    // player, keep the numeric fields, tell the user.
                    if (video && video.parentNode) video.parentNode.removeChild(video);
                    if (mini && mini.parentNode) mini.parentNode.removeChild(mini);
                    video = null; hereBtn = null; previewBtn = null; mini = null;
                    noPreview.style.display = "block";
                });
                hereBtn.onclick = function () {
                    state.start = video.currentTime || 0; errEl.textContent = ""; render();
                };
                previewBtn.onclick = function () {
                    var v0 = parseTime(startRow.txt.value); if (!isNaN(v0)) state.start = v0;
                    render();
                    try {
                        video.currentTime = state.start;
                        stopAt = state.start + state.len;
                        var pr = video.play();
                        if (pr && pr.catch) pr.catch(function () {});
                    } catch (e) {}
                };
            }

            var done = false;
            function close(result) {
                if (done) return;
                done = true;
                document.removeEventListener("keydown", onKey, true);
                try { if (video) video.pause(); } catch (e) {}
                try { if (objUrl) URL.revokeObjectURL(objUrl); } catch (e) {}
                if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
                try { if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus(); } catch (e) {}
                resolve(result);
            }
            function accept() {
                var s = parseTime(startRow.txt.value);
                if (isNaN(s)) { errEl.textContent = T.trimBadStart; startRow.txt.focus(); return; }
                var l = parseTime(lenRow.txt.value);
                if (isNaN(l) || l < lenMin - 0.05 || l > lenMax + 0.05) {
                    errEl.textContent = T.trimBadLen.replace("{min}", String(round1(lenMin))).replace("{max}", String(round1(lenMax)));
                    lenRow.txt.focus(); return;
                }
                state.start = s; state.len = l; render();   // clamps into the file's real length
                close({ start: round3(state.start), end: round3(state.start + state.len) });
            }
            function dismiss() { close(null); }
            function onKey(e) {
                if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); dismiss(); return; }
                if (e.key === "Enter" && e.target && e.target.tagName === "INPUT" && e.target.type === "text") { e.preventDefault(); accept(); return; }
                if (e.key === "Tab") {
                    var f = box.querySelectorAll("input,button");
                    if (!f.length) return;
                    var first = f[0], last = f[f.length - 1];
                    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
                    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
                    else if (!box.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
                }
            }
            okBtn.onclick = accept;
            cancelBtn.onclick = dismiss;
            overlay.addEventListener("mousedown", function (e) { if (e.target === overlay) dismiss(); });
            document.addEventListener("keydown", onKey, true);
            setTimeout(function () { try { okBtn.focus(); } catch (e) {} }, 0);
        });
    }

    // ---- English subtitle file: how should it be used? (short dub + long dub) ----
    var SUBS_TEXT = {
        en: { title: "Use this subtitle file",
              intro: "Lisan AI finds your video's speech inside the subtitle (it can be the subtitle of a whole film), fixes wrong words in the transcript and ignores everything else. The subtitle's wording replaces what the AI heard. How should the lines be written?",
              autoT: "Smart (recommended)", autoD: "A new line starts where the subtitle shows another speaker (a dash). Everything else follows the AI's own lines.",
              joinT: "Together", joinD: "Keep the AI's lines as they are. A subtitle that has two lines is written together as one sentence.",
              linesT: "Separate, one line per subtitle line", linesD: "Every line of the subtitle becomes its own line. Use it when each subtitle line is a different speaker.",
              add: "Also add subtitle lines the AI didn't hear (only inside your video)",
              use: "Use this subtitle" },
        ar: { title: "استخدام ملف الترجمة",
              intro: "يبحث Lisan AI عن كلام الفيديو داخل ملف الترجمة (ويمكن أن يكون ترجمة فيلم كامل)، ويصحّح الكلمات الخاطئة في النص ويتجاهل كل ما عداه. تحلّ كلمات الترجمة محلّ ما سمعه الذكاء الاصطناعي. كيف تريد كتابة الأسطر؟",
              autoT: "ذكي (موصى به)", autoD: "يبدأ سطر جديد حيث تُظهر الترجمة متحدثًا آخر (شرطة). وما عدا ذلك يتبع أسطر الذكاء الاصطناعي.",
              joinT: "معًا", joinD: "تبقى الأسطر كما قسّمها الذكاء الاصطناعي. والترجمة ذات السطرين تُكتب معًا كجملة واحدة.",
              linesT: "منفصلة، سطر لكل سطر في الترجمة", linesD: "يصبح كل سطر في الترجمة سطرًا مستقلًا. استخدمه عندما يكون كل سطر لمتحدث مختلف.",
              add: "أضف أيضًا أسطر الترجمة التي لم يسمعها الذكاء الاصطناعي (داخل الفيديو فقط)",
              use: "استخدم هذه الترجمة" }
    };

    function showSubtitle(file, opts) {
        opts = opts || {};
        return new Promise(function (resolve) {
            injectStyle();
            var T = TEXT[isArabic() ? "ar" : "en"], S = SUBS_TEXT[isArabic() ? "ar" : "en"];
            var previouslyFocused = document.activeElement;
            var overlay = document.createElement("div");
            overlay.className = "ld-overlay";
            var box = document.createElement("div");
            box.className = "ld-box ld-wide" + (isArabic() ? " ld-rtl" : "");
            box.setAttribute("role", "dialog");
            box.setAttribute("aria-modal", "true");
            var title = document.createElement("h3");
            title.className = "ld-title";
            title.textContent = S.title;
            box.appendChild(title);
            var fn = document.createElement("p");
            fn.className = "ld-fname";
            fn.textContent = (file && file.name) ? String(file.name) : "";
            box.appendChild(fn);
            var msg = document.createElement("p");
            msg.className = "ld-msg";
            msg.style.marginBottom = "12px";
            msg.textContent = S.intro;
            box.appendChild(msg);

            var saved = "auto";
            try { saved = localStorage.getItem("lisan_subs_mode") || "auto"; } catch (e) {}
            if (saved !== "auto" && saved !== "join" && saved !== "lines") saved = "auto";
            var group = "ldsub" + Date.now();
            var labels = [];
            [["auto", S.autoT, S.autoD], ["join", S.joinT, S.joinD], ["lines", S.linesT, S.linesD]].forEach(function (o) {
                var lab = document.createElement("label");
                lab.className = "ld-opt";
                var r = document.createElement("input");
                r.type = "radio"; r.name = group; r.value = o[0]; r.checked = (o[0] === saved);
                var box2 = document.createElement("span");
                var b = document.createElement("b"); b.textContent = o[1];
                var d = document.createElement("i"); d.textContent = o[2];
                box2.appendChild(b); box2.appendChild(d);
                lab.appendChild(r); lab.appendChild(box2);
                box.appendChild(lab);
                labels.push(lab);
                r.onchange = refresh;
            });
            function refresh() {
                labels.forEach(function (l) { l.className = "ld-opt" + (l.querySelector("input").checked ? " ld-on" : ""); });
            }
            refresh();
            var addBox = null;
            if (opts.showAdd) {
                var cl = document.createElement("label");
                cl.className = "ld-chk";
                addBox = document.createElement("input");
                addBox.type = "checkbox";
                var ct = document.createElement("span"); ct.textContent = S.add;
                cl.appendChild(addBox); cl.appendChild(ct);
                cl.style.marginTop = "6px";
                box.appendChild(cl);
            }
            var btns = document.createElement("div");
            btns.className = "ld-btns";
            var cancelBtn = document.createElement("button");
            cancelBtn.type = "button"; cancelBtn.className = "ld-btn ld-cancel"; cancelBtn.textContent = T.cancel;
            var okBtn = document.createElement("button");
            okBtn.type = "button"; okBtn.className = "ld-btn ld-ok"; okBtn.textContent = S.use;
            btns.appendChild(cancelBtn); btns.appendChild(okBtn);
            box.appendChild(btns);
            overlay.appendChild(box);
            document.body.appendChild(overlay);

            var done = false;
            function close(result) {
                if (done) return;
                done = true;
                document.removeEventListener("keydown", onKey, true);
                if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
                try { if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus(); } catch (e) {}
                resolve(result);
            }
            function accept() {
                var mode = "auto";
                labels.forEach(function (l) { var i = l.querySelector("input"); if (i.checked) mode = i.value; });
                try { localStorage.setItem("lisan_subs_mode", mode); } catch (e) {}
                close({ mode: mode, add_missed: !!(addBox && addBox.checked) });
            }
            function dismiss() { close(null); }
            function onKey(e) {
                if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); dismiss(); return; }
                if (e.key === "Tab") {
                    var f = box.querySelectorAll("input,button");
                    if (!f.length) return;
                    var first = f[0], last = f[f.length - 1];
                    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
                    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
                    else if (!box.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
                }
            }
            okBtn.onclick = accept;
            cancelBtn.onclick = dismiss;
            overlay.addEventListener("mousedown", function (e) { if (e.target === overlay) dismiss(); });
            document.addEventListener("keydown", onKey, true);
            setTimeout(function () { try { okBtn.focus(); } catch (e) {} }, 0);
        });
    }

    // A subtitle file as text. Most are UTF-8; some come as UTF-16 or old Windows text; all of them are read right.
    function readSubtitleFile(file) {
        return new Promise(function (resolve, reject) {
            var fr = new FileReader();
            fr.onerror = function () { reject(new Error("read")); };
            fr.onload = function () {
                var u8 = new Uint8Array(fr.result), txt = "";
                try {
                    if (u8.length >= 2 && u8[0] === 0xFF && u8[1] === 0xFE) txt = new TextDecoder("utf-16le").decode(u8);
                    else if (u8.length >= 2 && u8[0] === 0xFE && u8[1] === 0xFF) txt = new TextDecoder("utf-16be").decode(u8);
                    else {
                        try { txt = new TextDecoder("utf-8", { fatal: true }).decode(u8); }
                        catch (e2) { txt = new TextDecoder("windows-1252").decode(u8); }
                    }
                } catch (e) { reject(e); return; }
                resolve(txt);
            };
            fr.readAsArrayBuffer(file);
        });
    }

    var api = {
        alert: function (message, opts) { return enqueue(function () { return show("alert", message, opts); }); },
        confirm: function (message, opts) { return enqueue(function () { return show("confirm", message, opts); }); },
        prompt: function (message, opts) { return enqueue(function () { return show("prompt", message, opts); }); },
        trim: function (file, opts) { return enqueue(function () { return showTrim(file, opts); }); },
        subtitle: function (file, opts) { return enqueue(function () { return showSubtitle(file, opts); }); },
        readSubtitle: readSubtitleFile
    };
    window.LisanDialog = api;
    window.lisanAlert = api.alert;
    window.lisanConfirm = api.confirm;
    window.lisanPrompt = api.prompt;
})();


/* Planned-maintenance banner. The admin sets a From/To window (Admin -> Settings);
   this shows a bar at the top of the page with those times in the visitor's own
   time zone, from the moment it is switched on until the window ends. It can be
   dismissed for the rest of the visit. Not shown on the admin page itself. */
(function () {
    if (window.__lisanMaintBanner) return;
    window.__lisanMaintBanner = true;
    if (/^\/admin/.test(location.pathname)) return;

    var data = null, bar = null, timer = null;

    function ar() {
        try {
            if (window.currentLang) return window.currentLang === "ar";
            if (document.documentElement.lang === "ar") return true;
            return (localStorage.getItem("lisan_lang") || "") === "ar";
        } catch (e) { return false; }
    }
    function fmt(iso) {
        try {
            return new Intl.DateTimeFormat(ar() ? "ar" : undefined, {
                weekday: "short", day: "numeric", month: "short",
                hour: "2-digit", minute: "2-digit", timeZoneName: "short"
            }).format(new Date(iso));
        } catch (e) { return iso; }
    }
    function dismissKey() { return "lisan_maint_" + data.start + "_" + data.end; }
    function dismissed() { try { return sessionStorage.getItem(dismissKey()) === "1"; } catch (e) { return false; } }

    function text() {
        var now = Date.now(), s = Date.parse(data.start), e = Date.parse(data.end);
        var from = fmt(data.start), to = fmt(data.end), note = data.message ? " " + data.message : "";
        if (ar()) {
            return (now >= s)
                ? "🛠️ صيانة جارية: قد يكون Lisan AI غير متاح لفترة قصيرة حتى " + to + "." + note
                : "🛠️ صيانة مجدولة: قد يكون Lisan AI غير متاح لفترة قصيرة بين " + from + " و " + to +
                  ". يُرجى عدم بدء مهمة طويلة قرب هذا الوقت، فقد تحتاج أي مهمة قيد التنفيذ إلى إعادة التشغيل." + note;
        }
        return (now >= s)
            ? "🛠️ Maintenance in progress: Lisan AI may be briefly unavailable until " + to + "." + note
            : "🛠️ Planned maintenance: Lisan AI may be briefly unavailable between " + from + " and " + to +
              ". Please avoid starting a long job close to that time — anything still running then may need to be restarted." + note;
    }

    function remove() { if (bar && bar.parentNode) bar.parentNode.removeChild(bar); bar = null; pad(); }

    function render() {
        if (!data || !data.active || Date.now() >= Date.parse(data.end) || dismissed()) { remove(); return; }
        if (!document.body) return;
        if (!bar) {
            bar = document.createElement("div");
            bar.id = "lisanMaintBanner";
            bar.setAttribute("role", "status");
            bar.style.cssText = "position:fixed;top:0;left:0;right:0;width:100%;z-index:99990;display:flex;gap:12px;align-items:flex-start;" +
                "justify-content:center;padding:10px 16px;background:#fef3c7;color:#78350f;border-bottom:1px solid #f59e0b;" +
                "font:600 13px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;text-align:center;box-sizing:border-box;margin:0;";
            var span = document.createElement("span"); span.id = "lisanMaintText"; span.style.cssText = "flex:1 1 auto;min-width:0;";
            var x = document.createElement("button");
            x.type = "button"; x.textContent = "×"; x.setAttribute("aria-label", "Close");
            x.style.cssText = "flex:0 0 auto;width:auto!important;display:block!important;box-sizing:content-box;border:0!important;border-radius:0;background:transparent;color:inherit;font-size:20px;line-height:1;cursor:pointer;padding:0 4px!important;margin:0!important;";
            x.onclick = function () { try { sessionStorage.setItem(dismissKey(), "1"); } catch (e) {} remove(); };
            bar.appendChild(span); bar.appendChild(x);
            document.body.appendChild(bar);
        }
        bar.dir = ar() ? "rtl" : "ltr";
        bar.firstChild.textContent = text();
        pad();
    }

    // The bar is fixed to the top of the window (works with every page layout);
    // push the page down by its height so it never covers the page's own header.
    var origPad = null;
    function pad() {
        if (!document.body) return;
        if (origPad === null) origPad = document.body.style.paddingTop || "";
        document.body.style.paddingTop = bar ? bar.offsetHeight + "px" : origPad;
    }

    function load() {
        fetch("/api/maintenance", { cache: "no-store" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d) { data = d; render(); } })
            .catch(function () {});
    }

    function start() {
        load();
        setInterval(load, 5 * 60 * 1000);     // pick up a newly switched-on notice
        timer = setInterval(render, 30 * 1000); // flip "planned" -> "in progress" -> gone
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
    else start();
})();

/* Lisan AI help tips -- ONE look for every "?" button and hover bubble on the
   main app, the Account page, Dub Long Video and Correct a long dub.

   API:
     LisanTip.attach(host, sources, {label})
         Adds a small "?" button at the end of `host` (usually a heading or a
         label). `sources` is an element, an array of elements, a string or a
         function returning a string. Source elements are hidden in the page
         and their CURRENT html is shown in the bubble each time it opens, so
         text that the page changes later (language switch, live numbers) is
         always up to date.
     data-tip-src            on a note element: it moves behind a "?" button
                             placed in data-tip-host (an element id) or, by
                             default, in the first heading of its card.
     data-tip="text"         on any element: plain hover/focus bubble, no "?".
     title="..."             on the top bar and round icon buttons is turned
                             into the same bubble automatically (no native
                             yellow box).
   Colors come from the page's CSS variables, so light/dark mode, Classic/New
   style and Arabic (right-to-left) all follow the page automatically. */
(function () {
    if (window.LisanTip) return;

    var CSS = [
        ".lt-src{display:none !important;}",
        "button.lt-btn,.lt-bubble{--lt-accent:var(--primary-fg,#4f46e5);}",
        "html body button.lt-btn{display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;flex:none;width:20px;height:20px;min-width:20px;max-width:20px;padding:0;margin:0 0 0 8px;margin-inline-start:8px;margin-inline-end:0;vertical-align:middle;position:relative;top:-1px;border-radius:50%;border:1.5px solid rgba(99,102,241,.4);border:1.5px solid color-mix(in srgb,var(--lt-accent,#6366f1) 42%,transparent);background:rgba(99,102,241,.1);background:color-mix(in srgb,var(--lt-accent,#6366f1) 10%,transparent);color:var(--lt-accent,#4f46e5);font-size:12px;line-height:1;cursor:help;box-shadow:none;transform:none !important;opacity:1;transition:background .15s,color .15s,border-color .15s,box-shadow .15s;}",
        "html body button.lt-btn svg{width:12px;height:12px;display:block;pointer-events:none;}",
        "html body button.lt-btn:hover,html body button.lt-btn[aria-expanded='true']{background:var(--lt-accent,#4f46e5);border-color:var(--lt-accent,#4f46e5);color:var(--card,#fff);box-shadow:0 2px 8px -2px rgba(79,70,229,.55);}",
        "html body button.lt-btn:focus-visible{outline:none;box-shadow:0 0 0 3px rgba(99,102,241,.35);}",
        ".lt-bubble{position:fixed;left:0;top:0;z-index:100001;box-sizing:border-box;width:max-content;max-width:min(360px,calc(100vw - 24px));padding:12px 15px 12px 17px;border-radius:12px;background:var(--card,#ffffff);color:var(--text,#1a1a2e);border:1px solid rgba(99,102,241,.35);border-color:color-mix(in srgb,var(--lt-accent,#6366f1) 32%,var(--border,#e5e7eb));box-shadow:0 14px 34px -10px rgba(15,23,42,.34),0 2px 8px rgba(15,23,42,.1);font-size:13px;line-height:1.6;font-weight:400;text-align:start;white-space:normal;overflow-wrap:anywhere;opacity:0;visibility:hidden;transform:translateY(5px);transition:opacity .15s ease,transform .15s ease,visibility 0s linear .15s;pointer-events:none;}",
        ".lt-bubble.lt-on{opacity:1;visibility:visible;transform:none;transition:opacity .15s ease,transform .15s ease;pointer-events:auto;}",
        ".lt-bubble.lt-above{transform:translateY(-5px);}",
        ".lt-bubble.lt-above.lt-on{transform:none;}",
        ".lt-bubble::before{content:'';position:absolute;inset-block:11px;inset-inline-start:0;width:3px;border-radius:0 3px 3px 0;background:linear-gradient(180deg,var(--lt-accent,#6366f1),var(--accent,#38bdf8));}",
        "[dir=rtl] .lt-bubble::before{border-radius:3px 0 0 3px;}",
        ".lt-bubble .lt-arrow{position:absolute;width:10px;height:10px;margin-left:-5px;background:var(--card,#ffffff);border:1px solid rgba(99,102,241,.35);border-color:color-mix(in srgb,var(--lt-accent,#6366f1) 32%,var(--border,#e5e7eb));transform:rotate(45deg);}",
        ".lt-bubble:not(.lt-above) .lt-arrow{top:-6px;border-right:0;border-bottom:0;}",
        ".lt-bubble.lt-above .lt-arrow{bottom:-6px;border-left:0;border-top:0;}",
        ".lt-bubble p{margin:0;}",
        ".lt-bubble p+p,.lt-bubble ol,.lt-bubble ul{margin-top:8px;}",
        ".lt-bubble ol,.lt-bubble ul{margin-bottom:0;padding-inline-start:20px;padding-inline-end:0;}",
        ".lt-bubble li+li{margin-top:4px;}",
        ".lt-bubble strong{font-weight:600;color:var(--text,#1a1a2e);}",
        ".lt-bubble a{color:var(--primary-fg,#3949ab);}",
        "@media (prefers-reduced-motion:reduce){.lt-bubble,.lt-bubble.lt-on{transition:none;}}"
    ].join("\n");

    var ICON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5.7 6.2a2.4 2.4 0 1 1 3.8 1.95C8.6 8.8 8 9.3 8 10.4"/><circle cx="8" cy="13" r=".55" fill="currentColor"/></svg>';
    var AUTO_SEL = "#userBar a[title],#userBar button[title],#userBar span[title],.contact-icon-btn[title],#darkModeBtn[title],.btn-logout[title],#timelineWrap [title]";
    var registry = [], bubble = null, arrow = null, body = null, cur = null, pinned = false, showT = 0, hideT = 0, uid = 0;

    function addCss() {
        if (document.getElementById("lt-css")) return;
        var s = document.createElement("style"); s.id = "lt-css"; s.textContent = CSS;
        (document.head || document.documentElement).appendChild(s);
    }
    function isAr() {
        return document.documentElement.lang === "ar" || document.documentElement.dir === "rtl" ||
            (document.body && document.body.classList.contains("lang-ar"));
    }
    function ensureBubble() {
        if (bubble) return;
        bubble = document.createElement("div"); bubble.className = "lt-bubble"; bubble.id = "ltBubble"; bubble.setAttribute("role", "tooltip");
        arrow = document.createElement("span"); arrow.className = "lt-arrow";
        body = document.createElement("div"); body.className = "lt-body";
        bubble.appendChild(arrow); bubble.appendChild(body); document.body.appendChild(bubble);
        bubble.addEventListener("mouseenter", function () { clearTimeout(hideT); });
        bubble.addEventListener("mouseleave", function () { if (!pinned) schedHide(); });
    }
    function cleanHtml(h) {
        return String(h || "").replace(/^\s*(?:💡|👉|ℹ️|ⓘ)\s*/, "").trim();
    }
    function contentOf(entry) {
        var parts = [];
        entry.sources.forEach(function (s) {
            var h = "";
            if (typeof s === "function") h = s();
            else if (typeof s === "string") h = s;
            else if (s && s.nodeType === 1) {
                h = s.innerHTML;
                if (s.tagName === "OL" || s.tagName === "UL") { parts.push("<" + s.tagName.toLowerCase() + ">" + h + "</" + s.tagName.toLowerCase() + ">"); return; }
            }
            h = cleanHtml(h);
            if (h) parts.push("<p>" + h + "</p>");
        });
        return parts.join("");
    }
    function place() {
        if (!cur || !bubble) return;
        var r = cur.el.getBoundingClientRect(), vw = document.documentElement.clientWidth, vh = window.innerHeight;
        var w = bubble.offsetWidth, h = bubble.offsetHeight, gap = 11;
        var left = r.left + r.width / 2 - w / 2;
        left = Math.max(8, Math.min(left, vw - w - 8));
        var below = r.bottom + gap + h <= vh - 8 || r.top - gap - h < 8;
        var top = below ? r.bottom + gap : r.top - gap - h;
        bubble.classList.toggle("lt-above", !below);
        bubble.style.left = Math.round(left) + "px"; bubble.style.top = Math.round(top) + "px";
        var ax = r.left + r.width / 2 - left; ax = Math.max(16, Math.min(ax, w - 16));
        arrow.style.left = Math.round(ax) + "px";
    }
    function show(entry, pin) {
        ensureBubble(); clearTimeout(showT); clearTimeout(hideT);
        var html = entry.text != null ? null : contentOf(entry);
        if (entry.text != null) body.textContent = entry.text; else { if (!html) return; body.innerHTML = html; }
        bubble.dir = entry.text != null ? "auto" : (isAr() ? "rtl" : "ltr");
        if (cur && cur.el !== entry.el) cur.el.setAttribute("aria-expanded", "false");
        cur = entry; pinned = !!pin;
        if (entry.el.getAttribute("aria-haspopup") === "true") entry.el.setAttribute("aria-expanded", "true");
        entry.el.setAttribute("aria-describedby", "ltBubble");
        bubble.classList.add("lt-on"); place();
    }
    function hide() {
        clearTimeout(showT); clearTimeout(hideT);
        if (bubble) bubble.classList.remove("lt-on");
        if (cur) { cur.el.setAttribute("aria-expanded", "false"); cur.el.removeAttribute("aria-describedby"); }
        cur = null; pinned = false;
    }
    function schedHide() { clearTimeout(hideT); hideT = setTimeout(hide, 140); }
    function schedShow(entry) { clearTimeout(hideT); clearTimeout(showT); showT = setTimeout(function () { show(entry, false); }, 90); }

    function bind(entry, clickable) {
        var el = entry.el;
        el.addEventListener("mouseenter", function () { if (!pinned) schedShow(entry); });
        el.addEventListener("mouseleave", function () { clearTimeout(showT); if (!pinned) schedHide(); });
        el.addEventListener("focus", function () { if (!pinned) schedShow(entry); });
        el.addEventListener("blur", function () { if (!pinned) schedHide(); });
        if (clickable) el.addEventListener("click", function (e) {
            e.preventDefault(); e.stopPropagation();
            if (cur && cur.el === el && pinned) hide(); else show(entry, true);
        });
    }

    function attach(host, sources, opts) {
        if (!host) return null;
        addCss();
        var list = (Array.isArray(sources) ? sources : [sources]).filter(function (s) { return s != null; });
        list.forEach(function (s) { if (s && s.nodeType === 1) { s.classList.add("lt-src"); s.setAttribute("data-tip-done", "1"); } });
        var existing = host.querySelector(":scope > button.lt-btn");
        if (existing && existing._lt) { existing._lt.sources = existing._lt.sources.concat(list); return existing; }
        var b = document.createElement("button"); b.type = "button"; b.className = "lt-btn";
        b.setAttribute("aria-haspopup", "true"); b.setAttribute("aria-expanded", "false");
        b.setAttribute("aria-label", (opts && opts.label) || (isAr() ? "مساعدة" : "Help"));
        b.innerHTML = ICON;
        var entry = { el: b, sources: list }; b._lt = entry;
        bind(entry, true);
        entry.host = host; registry.push(entry); host.appendChild(b);
        return b;
    }

    function hostFor(src) {
        var id = src.getAttribute("data-tip-host");
        if (id) { var h = document.getElementById(id) || document.querySelector(id); if (h) return h; }
        var card = src.closest(".card");
        return (card && card.querySelector("h3")) || null;
    }
    function scan(root) {
        if (cur && !document.documentElement.contains(cur.el)) hide();
        registry.forEach(function (e) { if (e.el.parentNode !== e.host && document.documentElement.contains(e.host)) e.host.appendChild(e.el); });
        (root || document).querySelectorAll("[data-tip-src]:not([data-tip-done])").forEach(function (src) {
            var host = hostFor(src); if (host) attach(host, src);
        });
        (root || document).querySelectorAll("[data-tip]:not([data-lt-bound])").forEach(function (el) {
            el.setAttribute("data-lt-bound", "1");
            var entry = { el: el, sources: [], text: el.getAttribute("data-tip") };
            Object.defineProperty(entry, "text", { get: function () { return el.getAttribute("data-tip"); } });
            bind(entry, false);
        });
        (root || document).querySelectorAll(AUTO_SEL).forEach(convertTitle);
    }
    function convertTitle(el) {
        var t = el.getAttribute && el.getAttribute("title");
        if (!t) return;
        if (!el.getAttribute("aria-label") && !el.textContent.trim()) el.setAttribute("aria-label", t);
        el.removeAttribute("title"); el.setAttribute("data-tip", t);
        if (!el.hasAttribute("data-lt-bound")) scan_one(el);
    }
    function scan_one(el) {
        el.setAttribute("data-lt-bound", "1");
        var entry = { el: el, sources: [] };
        Object.defineProperty(entry, "text", { get: function () { return el.getAttribute("data-tip"); } });
        bind(entry, false);
    }

    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && cur) { var b = cur.el; hide(); if (b.focus && b.classList.contains("lt-btn")) b.focus(); } });
    document.addEventListener("click", function (e) { if (cur && pinned && !(bubble && bubble.contains(e.target)) && e.target !== cur.el && !cur.el.contains(e.target)) hide(); });
    window.addEventListener("resize", place); window.addEventListener("scroll", place, true);

    var pend = 0;
    function later() { if (pend) return; pend = setTimeout(function () { pend = 0; scan(); }, 30); }
    function start() {
        addCss(); scan();
        try {
            new MutationObserver(function (muts) {
                var need = false;
                for (var i = 0; i < muts.length; i++) {
                    var m = muts[i];
                    if (m.type === "attributes") { if (m.target.matches && m.target.matches(AUTO_SEL)) convertTitle(m.target); }
                    else if (m.addedNodes && m.addedNodes.length) need = true;
                }
                if (need) later();
            }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["title"] });
        } catch (e) {}
    }
    window.LisanTip = { attach: attach, scan: scan, hide: hide };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
