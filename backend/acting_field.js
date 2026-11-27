/* Lisan AI: "How it is said" under one line (1.110.0). Used by the short dub (/app), the long dub (/dub-long) and the long-dub
 * correction page (/dub-long-edit).
 *
 * The field shows the acting note the AI wrote from the original voice, or the customer's own direction. The customer may write in
 * Arabic or English, in their own words. The page's save(text) sends it to the server, which turns it into a short English note
 * for the voice (acting_direction.py) and answers {acting_text, acting_by, acting_note}. An empty field removes the customer's
 * direction (the AI's note, if any, is used again).
 *
 *   var f = LisanActing.field({lang: "ar", view: {acting_text, acting_by, acting_note}, save: function (text) { return promise; },
 *                              onSaved: function (view) {}});
 *   parent.appendChild(f.el);  f.set(view);  f.dropAi();   // dropAi: the line's style was changed by hand, the AI's note no longer fits
 * save() rejects with an Error whose .code is "acting_unclear" when the text could not be used. */
(function () {
  "use strict";
  if (window.LisanActing) return;

  var TXT = {
    en: { label: "How it is said", ph: "e.g. tired and quiet, stressing the last word",
          ai: "Written by the AI from the original voice. Change it if this line sounds wrong.",
          you: "Your direction (used instead of the voice style).", sent: " Sent to the voice as: ",
          empty: "Optional. Describe in your own words how this line should sound.", busy: "Preparing…",
          unclear: "This could not be turned into a voice direction. Describe only how the line is said, for example: tired and quiet, stressing the last word.",
          failed: "The direction could not be saved right now. Please try again in a moment." },
    ar: { label: "كيف تُقال الجملة", ph: "مثلاً: بصوت متعب وهادئ، مع التشديد على الكلمة الأخيرة",
          ai: "كتبها الذكاء الاصطناعي من الصوت الأصلي. غيّرها إذا لم يبدُ السطر صحيحًا.",
          you: "توجيهك (يُستخدم بدلاً من الأسلوب).", sent: " يُرسل إلى الصوت هكذا: ",
          empty: "اختياري. صِف بكلماتك كيف يجب أن يبدو هذا السطر.", busy: "جارٍ التجهيز…",
          unclear: "تعذّر تحويل هذا إلى توجيه للصوت. صِف فقط كيف تُقال الجملة، مثلاً: بصوت متعب وهادئ، مع التشديد على الكلمة الأخيرة.",
          failed: "تعذّر حفظ التوجيه الآن. حاول مرة أخرى بعد قليل." }
  };

  function style() {
    if (document.getElementById("lisanActingStyle")) return;
    var css = ".lisan-acting{display:flex;flex-direction:column;gap:3px;margin:6px 0 2px;width:100%;box-sizing:border-box}" +
      ".lisan-acting label{display:flex;align-items:center;gap:6px;font-size:12px;font-weight:600;color:inherit;margin:0}" +
      ".lisan-acting input{flex:1;min-width:0;font-size:13px;padding:5px 8px;border-radius:6px;box-sizing:border-box}" +
      ".lisan-acting small{font-size:11px;opacity:.75;line-height:1.35}" +
      ".lisan-acting small.err{color:#c62828;opacity:1}" +
      ".lisan-acting.by-you input{border-color:#7e57c2}";
    var el = document.createElement("style"); el.id = "lisanActingStyle"; el.textContent = css;
    document.head.appendChild(el);
  }

  function clean(v) { return String(v == null ? "" : v).replace(/\s+/g, " ").trim(); }

  function field(opts) {
    style();
    var t = TXT[opts.lang === "ar" ? "ar" : "en"];
    var view = { acting_text: "", acting_by: "", acting_note: "" };
    var wrap = document.createElement("div"); wrap.className = "lisan-acting"; wrap.setAttribute("data-i18n", "none");
    var lab = document.createElement("label"); lab.setAttribute("data-i18n", "none");
    var name = document.createElement("span"); name.textContent = t.label; name.setAttribute("data-i18n", "none");
    var input = document.createElement("input"); input.type = "text"; input.maxLength = 200; input.dir = "auto";
    input.placeholder = t.ph; input.setAttribute("aria-label", t.label); input.setAttribute("data-i18n", "none");
    lab.appendChild(name); lab.appendChild(input);
    var note = document.createElement("small"); note.setAttribute("data-i18n", "none"); note.setAttribute("aria-live", "polite");
    wrap.appendChild(lab); wrap.appendChild(note);

    function caption() {
      note.classList.remove("err");
      if (view.acting_by === "you") {
        var sent = clean(view.acting_note);
        note.textContent = t.you + (sent && sent.toLowerCase() !== clean(view.acting_text).toLowerCase() ? t.sent + sent : "");
      } else if (view.acting_by === "ai") note.textContent = t.ai;
      else note.textContent = t.empty;
      wrap.classList.toggle("by-you", view.acting_by === "you");
    }
    function set(v) {
      view = { acting_text: clean(v && v.acting_text), acting_by: (v && v.acting_by) || "", acting_note: clean(v && v.acting_note) };
      input.value = view.acting_text;
      caption();
    }
    var busy = false;
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); input.blur(); } });
    input.addEventListener("change", function () {
      var text = clean(input.value);
      if (busy || text === view.acting_text) { input.value = view.acting_text; return; }
      busy = true; input.disabled = true; note.classList.remove("err"); note.textContent = t.busy;
      Promise.resolve().then(function () { return opts.save(text); }).then(function (v) {
        set(v || {}); if (opts.onSaved) opts.onSaved(view);
      }, function (ex) {
        input.value = view.acting_text; caption();
        note.textContent = (ex && ex.code === "acting_unclear") ? t.unclear : t.failed; note.classList.add("err");
      }).then(function () { busy = false; input.disabled = false; });
    });
    set(opts.view || {});
    return {
      el: wrap, set: set, input: input,
      dropAi: function () { if (view.acting_by === "ai") set({}); }
    };
  }

  window.LisanActing = { field: field };
})();
