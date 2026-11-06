// Feedback question and invite links (Ali, Nov 2026; see feedback.py).
// 1. Invite links: lisanai.org/?ref=CODE remembers the code in this browser; once the visitor is logged in it is sent once.
// 2. Feedback: right after a finished result is downloaded, the server is asked whether to show the question (only users who
//    use the app a lot, once; closed without an answer: again after 7 days, at most twice). 1-5 stars and an optional text.
//    A good answer (4 or 5 stars) shows the person's own invite link: a friend who signs up with it gives them 50 credits.
(function () {
    var KEY = "lisan_invite";

    // ---- 1. invite links
    try {
        var m = /[?&]ref=([A-Za-z0-9]{6,16})(?:&|#|$)/.exec(location.search || "");
        if (m) localStorage.setItem(KEY, JSON.stringify({ code: m[1].toLowerCase(), at: Date.now() }));
        var saved = JSON.parse(localStorage.getItem(KEY) || "null");
        if (saved && saved.code) {
            if (Date.now() - (saved.at || 0) > 30 * 86400000) {
                localStorage.removeItem(KEY);
            } else if (!m) {          // the page that brought the link is the home page: nobody is logged in there yet
                fetch("/api/invite/claim", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ code: saved.code }) }).then(function (r) {
                    if (r.status === 200 || r.status === 400) localStorage.removeItem(KEY);     // logged out (401) or busy: next page
                }).catch(function () {});
            }
        }
    } catch (e) { /* the site works the same without it */ }

    // ---- 2. feedback question
    var T = {
        en: {
            title: "How do you like Lisan AI?", improve: "What should we improve? (optional)", send: "Send", later: "Later",
            stars: "Choose 1 to 5 stars.", thanks: "Thank you! We read every answer.",
            glad: "Thank you, we're glad you like it!",
            invite: function (c, n) { return "If a friend creates an account with your link, you get " + c + " credits as a thank-you (for up to " + n + " friends)."; },
            copy: "Copy link", copied: "Copied", whatsapp: "Share on WhatsApp", close: "Close", failed: "This could not be sent. Please try again.",
            share: "I use Lisan AI to turn videos and texts into natural Arabic speech. Try it: "
        },
        ar: {
            title: "ما رأيك في Lisan AI؟", improve: "ما الذي ينبغي أن نحسّنه؟ (اختياري)", send: "إرسال", later: "لاحقًا",
            stars: "اختر من نجمة واحدة إلى خمس نجوم.", thanks: "شكرًا لك! نقرأ كل إجابة.",
            glad: "شكرًا لك، يسعدنا أنه أعجبك!",
            invite: function (c, n) { return "إذا أنشأ صديق حسابًا عن طريق رابطك، تحصل على " + c + " رصيد هديةَ شكر (حتى " + n + " أصدقاء)."; },
            copy: "نسخ الرابط", copied: "تم النسخ", whatsapp: "مشاركة عبر واتساب", close: "إغلاق", failed: "تعذّر الإرسال. يرجى المحاولة مرة أخرى.",
            share: "أستخدم Lisan AI لتحويل الفيديوهات والنصوص إلى كلام عربي طبيعي. جرّبه: "
        }
    };
    var RESULT = /\/api\/(text_voice\/[0-9a-f]{8,}\/audio|download\/[^?#]*final[^?#]*|longdub\/[^\/?#]+\/download|my_jobs\/[^\/?#]+\/(video|lipsync|audio))(?:[?#]|$)/;
    var asked = false;

    function lang() { try { return localStorage.getItem("lisan_lang") === "ar" ? "ar" : "en"; } catch (e) { return "en"; } }
    function el(tag, css, text) { var e = document.createElement(tag); if (css) e.style.cssText = css; if (text) e.textContent = text; return e; }
    function page() { var p = location.pathname; return p.indexOf("text") >= 0 ? "text_voice" : p.indexOf("long") >= 0 ? "long_dub" : p.indexOf("account") >= 0 ? "account" : "short_dub"; }

    function show() {
        var t = T[lang()], rtl = lang() === "ar", stars = 0;
        var back = el("div", "position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:10000;display:flex;align-items:center;justify-content:center;padding:16px");
        var box = el("div", "background:var(--card,#fff);color:var(--text,#1a1a2e);border:1px solid var(--border,#e5e7eb);border-radius:12px;" +
            "max-width:420px;width:100%;padding:20px;box-shadow:0 10px 25px rgba(0,0,0,.2);font-family:inherit");
        box.dir = rtl ? "rtl" : "ltr";
        box.setAttribute("role", "dialog");
        box.setAttribute("aria-modal", "true");
        var h = el("h3", "margin:0 0 12px;font-size:18px", t.title);
        box.setAttribute("aria-label", t.title);
        var row = el("div", "display:flex;gap:6px;margin-bottom:12px;direction:ltr;justify-content:" + (rtl ? "flex-end" : "flex-start"));
        var btns = [];
        for (var i = 1; i <= 5; i++) {
            (function (n) {
                var b = el("button", "font-size:30px;line-height:1;background:none;border:0;cursor:pointer;padding:2px;color:var(--text-muted,#9ca3af)", "☆");
                b.type = "button";
                b.setAttribute("aria-label", n + " / 5");
                b.onclick = function () { stars = n; paint(); };
                btns.push(b);
                row.appendChild(b);
            })(i);
        }
        function paint() { btns.forEach(function (b, k) { b.textContent = k < stars ? "★" : "☆"; b.style.color = k < stars ? "#f59e0b" : "var(--text-muted,#9ca3af)"; }); }
        var area = el("textarea", "width:100%;min-height:80px;box-sizing:border-box;padding:8px;border:1px solid var(--border,#e5e7eb);border-radius:8px;" +
            "background:var(--input-bg,#fff);color:inherit;font:inherit;resize:vertical");
        area.placeholder = t.improve;
        area.maxLength = 2000;
        var note = el("p", "margin:8px 0 0;font-size:13px;color:var(--error,#dc2626);min-height:1em");
        var bar = el("div", "display:flex;gap:8px;margin-top:12px;justify-content:flex-end");
        var send = el("button", "padding:8px 16px;border:0;border-radius:8px;background:var(--primary,#1a237e);color:#fff;cursor:pointer;font:inherit", t.send);
        var later = el("button", "padding:8px 16px;border:1px solid var(--border,#e5e7eb);border-radius:8px;background:none;color:inherit;cursor:pointer;font:inherit", t.later);
        send.type = later.type = "button";
        function close() { if (back.parentNode) back.parentNode.removeChild(back); }
        later.onclick = close;
        back.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
        send.onclick = function () {
            if (!stars) { note.textContent = t.stars; return; }
            send.disabled = true;
            fetch("/api/feedback", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ stars: stars, text: area.value, page: page() }) })
                .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d || {} }; }); })
                .then(function (res) {
                    if (!res.ok) { send.disabled = false; note.textContent = res.d.error || t.failed; return; }
                    thanks(box, t, res.d.invite, close);
                }, function () { send.disabled = false; note.textContent = t.failed; });
        };
        bar.appendChild(later);
        bar.appendChild(send);
        [h, row, area, note, bar].forEach(function (x) { box.appendChild(x); });
        back.appendChild(box);
        back.addEventListener("click", function (e) { if (e.target === back) close(); });
        document.body.appendChild(back);
        btns[0].focus();
    }

    function thanks(box, t, invite, close) {
        while (box.firstChild) box.removeChild(box.firstChild);
        box.appendChild(el("h3", "margin:0 0 10px;font-size:18px", invite ? t.glad : t.thanks));
        if (invite && invite.link) {
            box.appendChild(el("p", "margin:0 0 10px;font-size:14px", t.invite(invite.credits, invite.max)));
            var link = el("input", "width:100%;box-sizing:border-box;padding:8px;border:1px solid var(--border,#e5e7eb);border-radius:8px;" +
                "background:var(--input-bg,#fff);color:inherit;font:inherit;direction:ltr");
            link.readOnly = true;
            link.value = invite.link;
            link.onclick = function () { link.select(); };
            box.appendChild(link);
            var row = el("div", "display:flex;gap:8px;margin-top:10px;flex-wrap:wrap");
            var copy = el("button", "padding:8px 14px;border:0;border-radius:8px;background:var(--primary,#1a237e);color:#fff;cursor:pointer;font:inherit", t.copy);
            copy.type = "button";
            copy.onclick = function () {
                function done() { copy.textContent = t.copied; }
                try { navigator.clipboard.writeText(invite.link).then(done, function () { link.select(); document.execCommand("copy"); done(); }); }
                catch (e) { link.select(); try { document.execCommand("copy"); done(); } catch (e2) {} }
            };
            var wa = el("a", "padding:8px 14px;border:1px solid var(--border,#e5e7eb);border-radius:8px;color:inherit;text-decoration:none", t.whatsapp);
            wa.href = "https://wa.me/?text=" + encodeURIComponent(t.share + invite.link);
            wa.target = "_blank";
            wa.rel = "noopener";
            row.appendChild(copy);
            row.appendChild(wa);
            box.appendChild(row);
        }
        var bar = el("div", "display:flex;justify-content:flex-end;margin-top:14px");
        var c = el("button", "padding:8px 16px;border:1px solid var(--border,#e5e7eb);border-radius:8px;background:none;color:inherit;cursor:pointer;font:inherit", t.close);
        c.type = "button";
        c.onclick = close;
        bar.appendChild(c);
        box.appendChild(bar);
        c.focus();
    }

    function maybeAsk() {
        if (asked) return;
        asked = true;                 // one question per page at most
        fetch("/api/feedback/ask", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : {}; })
            .then(function (d) { if (d && d.ask) setTimeout(show, 1200); }, function () {});
    }

    document.addEventListener("click", function (e) {
        var a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
        if (a && RESULT.test(a.getAttribute("href") || "")) maybeAsk();
    }, true);

    window.LisanFeedback = { maybeAsk: maybeAsk, _show: show };
})();
