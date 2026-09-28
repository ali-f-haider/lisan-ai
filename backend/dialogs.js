/* Lisan AI custom dialogs -- replaces the browser's own alert() / confirm() /
   prompt() message boxes everywhere in the app (main app, Account page,
   Pricing page, Admin panel), styled like the Buy Credits box.

   API (all return Promises, so callers `await` them or use .then):
     LisanDialog.alert(message, {title, okText, type})            -> Promise<void>
     LisanDialog.confirm(message, {title, okText, cancelText, danger}) -> Promise<boolean>
     LisanDialog.prompt(message, {title, value, placeholder, okText, cancelText, required})
                                                                   -> Promise<string|null>
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
        ".ld-rtl{direction:rtl;text-align:right;}",
        ".ld-rtl .ld-btns{flex-direction:row-reverse;}"
    ].join("\n");

    var TEXT = {
        en: { ok: "OK", cancel: "Cancel", alertTitle: "Notice", confirmTitle: "Please confirm", promptTitle: "Enter a value", required: "This field is required." },
        ar: { ok: "موافق", cancel: "إلغاء", alertTitle: "تنبيه", confirmTitle: "يرجى التأكيد", promptTitle: "أدخل قيمة", required: "هذا الحقل مطلوب." }
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

    var api = {
        alert: function (message, opts) { return enqueue(function () { return show("alert", message, opts); }); },
        confirm: function (message, opts) { return enqueue(function () { return show("confirm", message, opts); }); },
        prompt: function (message, opts) { return enqueue(function () { return show("prompt", message, opts); }); }
    };
    window.LisanDialog = api;
    window.lisanAlert = api.alert;
    window.lisanConfirm = api.confirm;
    window.lisanPrompt = api.prompt;
})();
