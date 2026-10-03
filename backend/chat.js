/* Lisan AI helper: the floating chat box. One self-contained file (no libraries).
   Loaded with <script src="/chat.js"></script> at the end of a page. Talks to POST /api/assistant.
   Every text from the server or the visitor is inserted with textContent, never as HTML. */
(function () {
  "use strict";
  if (window.__lisanChat) return;
  window.__lisanChat = true;

  var T = {
    en: {
      title: "Lisan AI helper", sub: "Ask me anything about Lisan AI", open: "Help chat", close: "Close", send: "Send",
      ph: "Type your question\u2026", hello: "Hi! I can explain how Lisan AI works and what things cost. When you are signed in I can also look at your own credits, jobs and files. What would you like to know?",
      s1: "How much does a long dub cost?", s2: "Why was I charged credits?", s3: "Where are my files?", s4: "My job seems stuck",
      thinking: "Thinking\u2026", err: "Something went wrong. Please try again, or write to the team.",
      support: "Send this to support", support_t: "Send to the support team", email: "Your email", note: "What should we know? (optional)",
      send_s: "Send to support", sent: "Sent. The team will reply to your email.", bad_email: "Please enter a valid email address.",
      fail_s: "Could not send. Please email contact@lisanai.org", clear: "New chat", ai: "AI answers can be wrong. For billing problems write to support."
    },
    ar: {
      title: "\u0645\u0633\u0627\u0639\u062f \u0644\u0633\u0627\u0646", sub: "\u0627\u0633\u0623\u0644\u0646\u064a \u0623\u064a \u0634\u064a\u0621 \u0639\u0646 \u0644\u0633\u0627\u0646", open: "\u0645\u0633\u0627\u0639\u062f\u0629", close: "\u0625\u063a\u0644\u0627\u0642", send: "\u0625\u0631\u0633\u0627\u0644",
      ph: "\u0627\u0643\u062a\u0628 \u0633\u0624\u0627\u0644\u0643\u2026", hello: "\u0623\u0647\u0644\u0627\u064b! \u0623\u0633\u062a\u0637\u064a\u0639 \u0634\u0631\u062d \u0637\u0631\u064a\u0642\u0629 \u0639\u0645\u0644 \u0644\u0633\u0627\u0646 \u0648\u0627\u0644\u0623\u0633\u0639\u0627\u0631. \u0648\u0639\u0646\u062f\u0645\u0627 \u062a\u0643\u0648\u0646 \u0645\u0633\u062c\u0651\u0644\u0627\u064b \u0627\u0644\u062f\u062e\u0648\u0644 \u0623\u0637\u0651\u0644\u0639 \u0623\u064a\u0636\u0627\u064b \u0639\u0644\u0649 \u0631\u0635\u064a\u062f\u0643 \u0648\u0645\u0634\u0627\u0631\u064a\u0639\u0643 \u0648\u0645\u0644\u0641\u0627\u062a\u0643. \u0628\u0645\u0627\u0630\u0627 \u0623\u0633\u0627\u0639\u062f\u0643\u061f",
      s1: "\u0643\u0645 \u062a\u0643\u0644\u0641\u0629 \u0627\u0644\u062f\u0628\u0644\u062c\u0629 \u0627\u0644\u0637\u0648\u064a\u0644\u0629\u061f", s2: "\u0644\u0645\u0627\u0630\u0627 \u062e\u064f\u0635\u0645\u062a \u0627\u0644\u0631\u0635\u064a\u062f\u061f", s3: "\u0623\u064a\u0646 \u0645\u0644\u0641\u0627\u062a\u064a\u061f", s4: "\u0645\u0634\u0631\u0648\u0639\u064a \u064a\u0628\u062f\u0648 \u0639\u0627\u0644\u0642\u0627\u064b",
      thinking: "\u062c\u0627\u0631\u064d \u0627\u0644\u062a\u0641\u0643\u064a\u0631\u2026", err: "\u062d\u062f\u062b \u062e\u0637\u0623. \u062d\u0627\u0648\u0644 \u0645\u0631\u0629 \u0623\u062e\u0631\u0649 \u0623\u0648 \u0631\u0627\u0633\u0644 \u0627\u0644\u0641\u0631\u064a\u0642.",
      support: "\u0623\u0631\u0633\u0644 \u0647\u0630\u0627 \u0625\u0644\u0649 \u0627\u0644\u062f\u0639\u0645", support_t: "\u0625\u0631\u0633\u0627\u0644 \u0625\u0644\u0649 \u0641\u0631\u064a\u0642 \u0627\u0644\u062f\u0639\u0645", email: "\u0628\u0631\u064a\u062f\u0643 \u0627\u0644\u0625\u0644\u0643\u062a\u0631\u0648\u0646\u064a", note: "\u0645\u0627 \u0627\u0644\u0630\u064a \u064a\u062c\u0628 \u0623\u0646 \u0646\u0639\u0631\u0641\u0647\u061f (\u0627\u062e\u062a\u064a\u0627\u0631\u064a)",
      send_s: "\u0625\u0631\u0633\u0627\u0644 \u0625\u0644\u0649 \u0627\u0644\u062f\u0639\u0645", sent: "\u062a\u0645 \u0627\u0644\u0625\u0631\u0633\u0627\u0644. \u0633\u064a\u0631\u062f\u0651 \u0639\u0644\u064a\u0643 \u0627\u0644\u0641\u0631\u064a\u0642 \u0639\u0628\u0631 \u0628\u0631\u064a\u062f\u0643.", bad_email: "\u064a\u0631\u062c\u0649 \u0625\u062f\u062e\u0627\u0644 \u0628\u0631\u064a\u062f \u0625\u0644\u0643\u062a\u0631\u0648\u0646\u064a \u0635\u062d\u064a\u062d.",
      fail_s: "\u062a\u0639\u0630\u0651\u0631 \u0627\u0644\u0625\u0631\u0633\u0627\u0644. \u0631\u0627\u0633\u0644\u0646\u0627 \u0639\u0644\u0649 contact@lisanai.org", clear: "\u0645\u062d\u0627\u062f\u062b\u0629 \u062c\u062f\u064a\u062f\u0629", ai: "\u0642\u062f \u062a\u0643\u0648\u0646 \u0625\u062c\u0627\u0628\u0627\u062a \u0627\u0644\u0630\u0643\u0627\u0621 \u0627\u0644\u0627\u0635\u0637\u0646\u0627\u0639\u064a \u062e\u0627\u0637\u0626\u0629. \u0644\u0645\u0634\u0627\u0643\u0644 \u0627\u0644\u062f\u0641\u0639 \u0631\u0627\u0633\u0644 \u0627\u0644\u062f\u0639\u0645."
    }
  };

  function lang() {
    try { var l = localStorage.getItem("lisan_lang"); if (l === "ar" || l === "en") return l; } catch (e) {}
    return (document.documentElement.lang || "en").slice(0, 2) === "ar" ? "ar" : "en";
  }
  function t(k) { return (T[lang()] || T.en)[k] || T.en[k] || k; }
  function store(op, k, v) {
    try { if (op === "get") return sessionStorage.getItem(k); if (op === "set") sessionStorage.setItem(k, v); if (op === "del") sessionStorage.removeItem(k); } catch (e) {}
    return null;
  }

  var css = [
    ".lch-btn{position:fixed;bottom:18px;right:18px;z-index:2147482000;width:56px;height:56px;border-radius:50%;border:0;cursor:pointer;background:#2563eb;color:#fff;box-shadow:0 6px 22px rgba(0,0,0,.28);display:flex;align-items:center;justify-content:center;transition:transform .15s}",
    ".lch-btn:hover{transform:scale(1.06)}.lch-btn svg{width:28px;height:28px}",
    ".lch-rtl .lch-btn{right:auto;left:18px}",
    ".lch-panel{position:fixed;bottom:86px;right:18px;z-index:2147482001;width:360px;max-width:calc(100vw - 24px);height:520px;max-height:calc(100vh - 110px);background:#fff;color:#1e293b;border-radius:16px;box-shadow:0 12px 40px rgba(0,0,0,.3);display:none;flex-direction:column;overflow:hidden;font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;border:1px solid #e2e8f0}",
    ".lch-rtl .lch-panel{right:auto;left:18px}",
    ".lch-panel.lch-on{display:flex}",
    ".lch-head{background:#2563eb;color:#fff;padding:12px 14px;display:flex;align-items:center;gap:10px}",
    ".lch-head b{display:block;font-size:15px}.lch-head span{font-size:12px;opacity:.85}",
    ".lch-head div{flex:1;min-width:0}",
    ".lch-x{background:transparent;border:0;color:#fff;font-size:20px;cursor:pointer;line-height:1;padding:4px 6px;border-radius:6px}",
    ".lch-x:hover{background:rgba(255,255,255,.18)}",
    ".lch-msgs{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px;background:#f8fafc}",
    ".lch-m{max-width:86%;padding:8px 11px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word;overflow-wrap:anywhere}",
    ".lch-bot{background:#fff;border:1px solid #e2e8f0;align-self:flex-start;border-bottom-left-radius:4px}",
    ".lch-me{background:#2563eb;color:#fff;align-self:flex-end;border-bottom-right-radius:4px}",
    ".lch-rtl .lch-bot{border-bottom-left-radius:12px;border-bottom-right-radius:4px}.lch-rtl .lch-me{border-bottom-right-radius:12px;border-bottom-left-radius:4px}",
    ".lch-dim{opacity:.7;font-style:italic}",
    ".lch-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:2px}",
    ".lch-chip{border:1px solid #2563eb;color:#2563eb;background:#fff;border-radius:14px;padding:4px 10px;font-size:12.5px;cursor:pointer}",
    ".lch-chip:hover{background:#eff6ff}",
    ".lch-sup{align-self:flex-start;border:1px solid #2563eb;background:#eff6ff;color:#1d4ed8;border-radius:10px;padding:6px 12px;cursor:pointer;font-size:13px}",
    ".lch-form{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:10px;align-self:stretch;display:flex;flex-direction:column;gap:6px}",
    ".lch-form input,.lch-form textarea{border:1px solid #cbd5e1;border-radius:8px;padding:7px 9px;font:inherit;color:#1e293b;background:#fff;width:100%;box-sizing:border-box}",
    ".lch-form button{background:#2563eb;color:#fff;border:0;border-radius:8px;padding:8px;cursor:pointer;font:inherit}",
    ".lch-note{font-size:12px;color:#64748b;padding:0 12px 4px;background:#f8fafc;text-align:center}",
    ".lch-in{display:flex;gap:6px;padding:10px;border-top:1px solid #e2e8f0;background:#fff;align-items:flex-end}",
    ".lch-in textarea{flex:1;resize:none;border:1px solid #cbd5e1;border-radius:10px;padding:8px 10px;font:inherit;max-height:90px;color:#1e293b;background:#fff}",
    ".lch-in button{background:#2563eb;color:#fff;border:0;border-radius:10px;padding:9px 13px;cursor:pointer;font:inherit}",
    ".lch-in button:disabled{opacity:.5;cursor:default}",
    ".lch-tools{display:flex;justify-content:flex-end;padding:0 12px;background:#f8fafc}",
    ".lch-tools a{font-size:12px;color:#64748b;cursor:pointer;text-decoration:underline}",
    "body.dark .lch-panel{background:#1e293b;color:#e2e8f0;border-color:#334155}",
    "body.dark .lch-msgs,body.dark .lch-note,body.dark .lch-tools{background:#0f172a}",
    "body.dark .lch-bot,body.dark .lch-form,body.dark .lch-chip,body.dark .lch-in{background:#1e293b;border-color:#334155;color:#e2e8f0}",
    "body.dark .lch-chip{color:#7dd3fc;border-color:#38bdf8}",
    "body.dark .lch-in textarea,body.dark .lch-form input,body.dark .lch-form textarea{background:#0f172a;color:#e2e8f0;border-color:#334155}",
    "body.dark .lch-sup{background:#0f172a;color:#7dd3fc;border-color:#38bdf8}",
    "@media (max-width:480px){.lch-panel{right:8px!important;left:8px!important;width:auto;bottom:80px;height:calc(100vh - 100px)}}"
  ].join("\n");

  var root, btn, panel, msgs, input, sendBtn, titleEl, subEl, noteEl, newEl, chipsEl;
  var history = [];            // {role:"user"|"assistant", text}
  var busy = false, lastSupport = false, opened = false;

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  function save() { store("set", "lisan_chat_v1", JSON.stringify(history.slice(-20))); }
  function load() { try { var h = JSON.parse(store("get", "lisan_chat_v1") || "[]"); if (Array.isArray(h)) history = h.filter(function (m) { return m && m.text; }); } catch (e) { history = []; } }

  function scroll() { msgs.scrollTop = msgs.scrollHeight; }
  function addMsg(role, text, cls) { var m = el("div", "lch-m " + (role === "user" ? "lch-me" : "lch-bot") + (cls ? " " + cls : ""), text); m.setAttribute("dir", "auto"); msgs.appendChild(m); scroll(); return m; }

  function renderAll() {
    msgs.innerHTML = "";
    if (!history.length) {
      addMsg("bot", t("hello"));
      chipsEl = el("div", "lch-chips");
      ["s1", "s2", "s3", "s4"].forEach(function (k) {
        var c = el("button", "lch-chip", t(k)); c.type = "button";
        c.onclick = function () { ask(t(k)); }; chipsEl.appendChild(c);
      });
      msgs.appendChild(chipsEl);
    } else {
      history.forEach(function (m) { addMsg(m.role, m.text); });
      if (lastSupport) addSupportButton();
    }
  }

  function applyLang() {
    var ar = lang() === "ar";
    root.className = ar ? "lch-rtl" : "";
    root.setAttribute("dir", ar ? "rtl" : "ltr");
    titleEl.textContent = t("title"); subEl.textContent = t("sub"); input.placeholder = t("ph");
    sendBtn.textContent = t("send"); btn.setAttribute("aria-label", t("open")); btn.title = t("open");
    noteEl.textContent = t("ai"); newEl.textContent = t("clear");
  }

  function addSupportButton() {
    var b = el("button", "lch-sup", t("support")); b.type = "button";
    b.onclick = function () { b.remove(); showSupportForm(); };
    msgs.appendChild(b); scroll();
  }

  function transcript() {
    return history.slice(-12).map(function (m) { return (m.role === "user" ? "Visitor: " : "Helper: ") + m.text; }).join("\n\n");
  }

  function showSupportForm() {
    var f = el("div", "lch-form");
    f.appendChild(el("b", "", t("support_t")));
    var em = el("input"); em.type = "email"; em.placeholder = t("email"); em.autocomplete = "email";
    try { em.value = localStorage.getItem("lisan_chat_email") || ""; } catch (e) {}
    var ta = el("textarea"); ta.rows = 2; ta.placeholder = t("note");
    var go = el("button", "", t("send_s")); go.type = "button";
    var st = el("div", "lch-dim", "");
    go.onclick = function () {
      var v = (em.value || "").trim();
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) { st.textContent = t("bad_email"); return; }
      go.disabled = true;
      try { localStorage.setItem("lisan_chat_email", v); } catch (e) {}
      var jid = ""; try { jid = (typeof currentJobId !== "undefined" && currentJobId) ? String(currentJobId) : ""; } catch (e) {}
      var body = (ta.value ? ta.value.trim() + "\n\n" : "") + "--- Sent from the AI helper chat ---\nPage: " + location.pathname + (jid ? "\nShort-dub job: " + jid : "") + "\n\n" + transcript();
      fetch("/api/contact", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: "AI helper chat", email: v, message: body.slice(0, 4800), hp: "" }) })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok && j.ok !== false, j: j }; }); })
        .then(function (x) { if (x.ok) { f.remove(); addMsg("bot", t("sent")); } else { go.disabled = false; st.textContent = (x.j && x.j.error) || t("fail_s"); } })
        .catch(function () { go.disabled = false; st.textContent = t("fail_s"); });
    };
    [em, ta, go, st].forEach(function (n) { f.appendChild(n); });
    msgs.appendChild(f); scroll(); em.focus();
  }

  function ask(text) {
    text = (text || "").replace(/\s+/g, " ").trim();
    if (!text || busy) return;
    if (chipsEl && chipsEl.parentNode) chipsEl.remove();
    busy = true; sendBtn.disabled = true; lastSupport = false;
    history.push({ role: "user", text: text.slice(0, 1200) });
    addMsg("user", text); input.value = ""; autosize();
    var wait = addMsg("bot", t("thinking"), "lch-dim");
    var jid = ""; try { jid = (typeof currentJobId !== "undefined" && currentJobId) ? String(currentJobId) : ""; } catch (e) {}
    fetch("/api/assistant", {
      method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
      body: JSON.stringify({ messages: history.slice(-10), lang: lang(), page: location.pathname, job_id: jid })
    }).then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { s: r.status, j: j }; }); })
      .then(function (x) {
        wait.remove();
        var j = x.j || {};
        var ans = j.answer || (x.s === 429 ? "" : t("err"));
        if (!ans) ans = t("err");
        history.push({ role: "assistant", text: ans });
        addMsg("bot", ans);
        lastSupport = !!j.support || !j.answer;
        if (lastSupport) addSupportButton();
        save();
      }).catch(function () {
        wait.remove(); history.pop(); addMsg("bot", t("err")); lastSupport = true; addSupportButton();
      }).then(function () { busy = false; sendBtn.disabled = false; input.focus(); });
  }

  function autosize() { input.style.height = "auto"; input.style.height = Math.min(90, input.scrollHeight) + "px"; }

  function toggle(on) {
    opened = on == null ? !opened : on;
    panel.classList.toggle("lch-on", opened);
    btn.style.display = opened && window.innerWidth <= 480 ? "none" : "flex";
    if (opened) { setTimeout(function () { input.focus(); }, 50); scroll(); }
  }

  function build() {
    var st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
    root = el("div", ""); root.id = "lisanChat";
    btn = el("button", "lch-btn"); btn.type = "button";
    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1.1-4.2A8 8 0 1 1 21 12z"/><path d="M9 11h6M9 14h4"/></svg>';
    panel = el("div", "lch-panel"); panel.setAttribute("role", "dialog");
    var head = el("div", "lch-head"), hd = el("div", "");
    titleEl = el("b", ""); subEl = el("span", ""); hd.appendChild(titleEl); hd.appendChild(subEl);
    var x = el("button", "lch-x", "\u00d7"); x.type = "button"; x.setAttribute("aria-label", "close"); x.onclick = function () { toggle(false); };
    head.appendChild(hd); head.appendChild(x);
    msgs = el("div", "lch-msgs"); msgs.setAttribute("aria-live", "polite");
    var tools = el("div", "lch-tools"); newEl = el("a", "", ""); newEl.onclick = function () { history = []; lastSupport = false; store("del", "lisan_chat_v1"); renderAll(); };
    tools.appendChild(newEl);
    noteEl = el("div", "lch-note", "");
    var inb = el("div", "lch-in");
    input = el("textarea"); input.setAttribute("dir", "auto"); input.rows = 1; input.maxLength = 1200;
    sendBtn = el("button", "", ""); sendBtn.type = "button";
    input.addEventListener("input", autosize);
    input.addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(input.value); } });
    sendBtn.onclick = function () { ask(input.value); };
    inb.appendChild(input); inb.appendChild(sendBtn);
    [head, msgs, tools, noteEl, inb].forEach(function (n) { panel.appendChild(n); });
    root.appendChild(btn); root.appendChild(panel); document.body.appendChild(root);
    btn.onclick = function () { toggle(); };
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && opened) toggle(false); });
    load(); applyLang(); renderAll();
    // follow a language switch made elsewhere on the page
    var last = lang();
    setInterval(function () { var l = lang(); if (l !== last) { last = l; applyLang(); if (!history.length) renderAll(); } }, 1000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build); else build();
})();
