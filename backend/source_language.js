/* Original language belongs to a project, independently of the interface locale. */
(function (root) {
    "use strict";
    var entries = [
        {code: "en", en: "English", ar: "الإنجليزية"},
        {code: "es", en: "Spanish", ar: "الإسبانية"},
        {code: "fr", en: "French", ar: "الفرنسية"},
        {code: "pt", en: "Portuguese", ar: "البرتغالية"},
        {code: "it", en: "Italian", ar: "الإيطالية"},
        {code: "de", en: "German", ar: "الألمانية"},
        {code: "nl", en: "Dutch", ar: "الهولندية"},
        {code: "pl", en: "Polish", ar: "البولندية"}
    ];
    function normalise(value) {
        var code = typeof value === "string" ? value.trim().toLowerCase() : "en";
        return entries.some(function (item) { return item.code === code; }) ? code : "en";
    }
    function name(code, locale) {
        code = normalise(code);
        return entries.filter(function (item) { return item.code === code; })[0][locale === "ar" ? "ar" : "en"];
    }
    function options(select, code, locale) {
        select.replaceChildren();
        entries.forEach(function (item) {
            var option = document.createElement("option");
            option.value = item.code;
            option.textContent = item[locale === "ar" ? "ar" : "en"];
            select.appendChild(option);
        });
        select.value = normalise(code);
    }
    function mount(target, locale) {
        if (!target) return;
        var select = document.getElementById("sourceLanguage");
        var code = select ? select.value : "en";
        if (!select) {
            var row = document.createElement("div");
            row.className = "ld-lipres";
            row.style.margin = "12px 0";
            var label = document.createElement("label");
            label.id = "sourceLanguageLabel";
            label.htmlFor = "sourceLanguage";
            select = document.createElement("select");
            select.id = "sourceLanguage";
            row.append(label, select);
            target.prepend(row);
        }
        document.getElementById("sourceLanguageLabel").textContent = locale === "ar" ? "لغة الفيديو الأصلية" : "Original video language";
        options(select, code, locale);
    }
    root.LisanSource = {entries: entries, normalise: normalise, name: name, options: options, mount: mount};
})(typeof window === "undefined" ? globalThis : window);
