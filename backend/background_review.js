/* Background outcomes only; provenance stays in the private catalogue. */
(function (root) {
  'use strict';
  function html(report, language) {
    if (!report || !Array.isArray(report.ambience_scenes)) return '';
    var ar = language === 'ar', tr = function(en, arabic) { return ar ? arabic : en; };
    var escape = function(value) { return String(value).replace(/[&<>"']/g, function(c) { return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]; }); };
    function time(value) {
      var n = Number(value);
      if (!Number.isFinite(n) || n < 0) return null;
      return Math.floor(n / 60) + ':' + String(Math.floor(n % 60)).padStart(2, '0');
    }
    var rows = [];
    report.ambience_scenes.forEach(function(s) {
      if (!s || typeof s !== 'object') return;
      var start = time(s.start), end = time(s.end);
      if (start === null || end === null || Number(s.end) <= Number(s.start)) return;
      var label = Number(s.filled_sec) > 0
        ? (s.replacement ? tr('Replacement ambience', 'أجواء بديلة') : tr('Original ambience extended', 'تمديد الأجواء الأصلية'))
        : (s.review ? tr('Check background', 'راجع الخلفية') : tr('Original background kept', 'الإبقاء على الخلفية الأصلية'));
      rows.push('<li><span dir="ltr">' + start + '–' + end + '</span> · ' + escape(label) + '</li>');
    });
    (Array.isArray(report.gaps) ? report.gaps : []).forEach(function(g) {
      if (!g || g.optional) return;
      var start = time(g.start), end = time(g.end);
      if (start === null || end === null) return;
      rows.push('<li><span dir="ltr">' + start + '–' + end + '</span> · ' + escape(g.ok ? tr('Music restored', 'استعادة الموسيقى') : tr('Check music', 'راجع الموسيقى')) + '</li>');
    });
    if (!rows.length) return '';
    return '<details class="ld-plain-note background-review" dir="' + (ar ? 'rtl' : 'ltr') + '"><summary>' +
      escape(tr('Background details', 'تفاصيل الخلفية')) + '</summary><p>' +
      escape(tr('Ambience matching is free. Listen to replacement sections and any sections marked for review.', 'مطابقة الأجواء مجانية. استمع إلى الأجزاء البديلة والأجزاء التي تحتاج مراجعة.')) +
      '</p><ul>' + rows.join('') + '</ul></details>';
  }
  root.LisanBackground = {html: html};
  if (typeof module !== 'undefined' && module.exports) module.exports = root.LisanBackground;
})(typeof window !== 'undefined' ? window : globalThis);
