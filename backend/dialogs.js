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

    var api = {
        alert: function (message, opts) { return enqueue(function () { return show("alert", message, opts); }); },
        confirm: function (message, opts) { return enqueue(function () { return show("confirm", message, opts); }); },
        prompt: function (message, opts) { return enqueue(function () { return show("prompt", message, opts); }); },
        trim: function (file, opts) { return enqueue(function () { return showTrim(file, opts); }); }
    };
    window.LisanDialog = api;
    window.lisanAlert = api.alert;
    window.lisanConfirm = api.confirm;
    window.lisanPrompt = api.prompt;
})();
