from pathlib import Path

p = Path("help.html")
if p.exists():
    Path("help_broken_backup.html").write_bytes(p.read_bytes())
    print("backed up current help.html -> help_broken_backup.html")

HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lisan AI — Help &amp; FAQ</title>
<link rel="icon" type="image/png" href="/logo.png">
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f9fafb;color:#1f2937;margin:0;line-height:1.65}
.top{background:#fff;border-bottom:1px solid #e5e7eb;padding:14px 22px;position:sticky;top:0;z-index:5}
.top a{color:#1a237e;font-weight:700;text-decoration:none}
.wrap{max-width:860px;margin:0 auto;padding:26px 22px 60px}
h1{color:#1a237e;font-size:30px;margin:6px 0 2px}
.sub{color:#6b7280;margin:0 0 14px}
.langs{display:flex;gap:10px;margin:10px 0 22px}
.langs button{padding:8px 18px;border-radius:10px;border:1px solid #e5e7eb;background:#fff;font-weight:700;color:#1a237e;cursor:pointer}
.langs button.on{background:#1a237e;color:#fff;border-color:#1a237e}
h2{color:#1a237e;font-size:22px;margin:26px 0 12px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px 20px;margin:0 0 14px;box-shadow:0 2px 8px rgba(0,0,0,.04)}
.card h3{margin:0 0 8px;color:#1a237e;font-size:16px}
.card p,.card li{color:#334155;font-size:14.5px}
.card ul{margin:6px 0 0;padding-left:20px}
.rtl{direction:rtl;text-align:right}
.rtl ul{padding-left:0;padding-right:20px}
</style>
</head>
<body>
<div class="top"><a href="/">&#8592; Back to App / العودة للتطبيق</a></div>
<div class="wrap">
<h1>Help &amp; FAQ</h1>
<p class="sub">المساعدة والأسئلة الشائعة</p>
<div class="langs"><button id="btnEn" class="on" onclick="showLang('en')">English</button><button id="btnAr" onclick="showLang('ar')">العربية</button></div>

<div id="secEn">
<h2>How It Works — Step by Step</h2>
<div class="card"><h3>Step 1: Upload</h3><p>Upload an audio or video file (MP3, WAV, MP4, AVI, MKV, MOV, WEBM). Maximum 60 seconds and 400 MB. The app transcribes the English speech and identifies each speaker automatically.</p></div>
<div class="card"><h3>Step 2: Edit Segments</h3><p>Review the transcription. You can:</p><ul>
<li><strong>Edit text</strong> — fix any transcription mistakes directly in the table.</li>
<li><strong>Auto Translate</strong> — translate all unlocked lines to Arabic using AI.</li>
<li><strong>Add Tashkeel</strong> — add Arabic diacritics (harakat) to the translated text.</li>
<li><strong>Detect Emotions</strong> — AI listens to each segment's voice tone and assigns a speaking style.</li>
<li><strong>Auto-Fix Timing</strong> — re-sync segment timestamps to match the original audio precisely.</li>
<li><strong>🔒 Lock</strong> — protect specific lines from being changed by Auto-Fix, Translate, or Tashkeel.</li></ul></div>
<div class="card"><h3>Step 3: Voice Cloning Setup</h3><p>The app analyzes how much speech each speaker has. Speakers with enough audio (≥1 second) can be cloned — the AI copies their unique voice from the video so the Arabic dub sounds like them.</p></div>
<div class="card"><h3>Step 3.5: Choose Speakers to Clone</h3><p>Review the quality guidance for each speaker. Uncheck any speaker you don't want to clone. Speakers with very little audio will produce poor clones — use a studio library voice instead.</p></div>
<div class="card"><h3>Step 4: Assign Voices</h3><p>Pick a voice for each speaker. You can use:</p><ul>
<li><strong>Cloned voices</strong> — copied from your video (if cloning succeeded).</li>
<li><strong>Studio library voices</strong> — high-quality professional voices (numbered, names hidden).</li></ul>
<p>Use 🎲 Auto-Assign to let the app pick the best match automatically. Two speakers never share the same numbered voice.</p></div>
<div class="card"><h3>Step 5: Generate Arabic Audio</h3><p>Click Generate Arabic Audio. The AI speaks each line in Arabic using the assigned voice and emotion/style. This may take 1–3 minutes depending on the clip length.</p></div>
<div class="card"><h3>Step 6: Review &amp; Fine-Tune</h3><p>Listen to the result. You can:</p><ul>
<li>🔄 <strong>Re-speak a single line</strong> — regenerate just one line without redoing the whole clip.</li>
<li>🎚️ <strong>Fine-Tune Timeline</strong> — drag segment blocks left/right (in milliseconds) to perfectly align with lip movements. Blocks cannot overlap.</li>
<li>🎬 <strong>Merge into Video</strong> — combine the dubbed audio with the original video and background music.</li>
<li>💾 <strong>Save / Load Project</strong> — save your work at any time as a JSON file. Load it later to continue editing without re-transcribing.</li></ul></div>

<h2>Frequently Asked Questions</h2>
<div class="card"><h3>What file formats are supported?</h3><p>Audio: MP3, WAV, FLAC, AAC, OGG. Video: MP4, AVI, MKV, MOV, WEBM. Maximum duration: 60 seconds. Maximum file size: 400 MB.</p></div>
<div class="card"><h3>How does voice cloning work?</h3><p>The AI extracts each speaker's voice characteristics from the original audio and creates a synthetic copy. This copy is then used to speak the Arabic translation in a voice that sounds similar to the original speaker. At least 1 second of clear speech is required; 10+ seconds produces the best results.</p></div>
<div class="card"><h3>What are credits and how are costs calculated?</h3><p>Credits are our internal billing unit: 1 credit = $0.01 USD (100 credits = $1.00). Translation and analysis cost a few cents per clip. Voice generation is charged by character count. Every button shows its estimated cost before you click it, and actual usage appears in the green box in Step 1.</p></div>
<div class="card"><h3>What does the 🔒 lock do?</h3><p>Locking a line protects it from three actions: Auto-Fix Timing, Auto Translate, and Add Tashkeel. Locked lines keep their current text, timing, and formatting untouched. Use this for lines you've manually perfected.</p></div>
<div class="card"><h3>Can I combine multiple emotion/style tags?</h3><p>Yes! Use the dropdown to add tags one at a time, or type directly in the text box (e.g., "confident, calm"). Only recognized tags from the official list are accepted; unrecognized words are removed automatically. Examples: "anxious, afraid", "playful, teasing", "calm, firm".</p></div>
<div class="card"><h3>Why does my cloned voice sound robotic?</h3><p>This usually means the speaker didn't have enough clear audio in the original clip. Check the quality guidance in Step 3.5 — if it says ⚠️ or ❌, switch that speaker to a studio library voice in Step 4 for better results.</p></div>
<div class="card"><h3>How does the timeline fine-tuning work?</h3><p>In Step 6, click 🎚️ Fine-Tune Timeline. Each blue block represents one spoken line. Drag blocks left or right to shift their timing by milliseconds. A time ruler at the top shows your position. Blocks stop at adjacent segments to prevent overlap. Click ✅ Confirm to rebuild the audio with your adjustments.</p></div>
<div class="card"><h3>Does the merged video keep the original background music?</h3><p>Yes. The app separates vocals from background audio during processing. When you merge, the dubbed Arabic vocals are mixed with the original background music/sound effects at balanced volumes.</p></div>
<div class="card"><h3>Can I save my progress and come back later?</h3><p>Yes. Click 💾 Save Project in Step 2 to download a JSON file with all your segments, translations, voice assignments, and settings. Later, click 📂 Load Project to restore everything exactly where you left off.</p></div>
<div class="card"><h3>Is lip-sync available?</h3><p>Not yet. Lip-sync (matching lip movements to the new language) is planned for a future update. Currently, the app produces high-quality dubbed audio that you can merge with your video.</p></div>
<div class="card"><h3>Will the resolution or quality of my video be affected?</h3><p>No. The original video stream is copied unchanged; only the audio track is replaced (or mixed) with the dubbed Arabic audio. Resolution, bitrate, frame rate, and visual quality stay exactly as your source file.</p></div>
<div class="card"><h3>Are my uploaded files and generated videos saved on your servers?</h3><p>No. Your uploaded files, transcriptions, and generated audio/video are stored temporarily on the server only while your session is active. They are automatically deleted when the server restarts or after a short period. You must download your results during your session — once you close the browser or the server recycles, the files are gone permanently. We do not keep copies of your media. For your safety, always click the download buttons in Step 6 before leaving the page.</p></div>
</div>

<div id="secAr" class="rtl" style="display:none">
<h2>كيف يعمل التطبيق — خطوة بخطوة</h2>
<div class="card"><h3>الخطوة ١: رفع الملف</h3><p>ارفع ملف صوتي أو مرئي (MP3، WAV، MP4، AVI، MKV، MOV، WEBM). الحد الأقصى ٦٠ ثانية و ٤٠٠ ميغابايت. يقوم التطبيق بنسخ الكلام الإنجليزي وتحديد كل متحدث تلقائياً.</p></div>
<div class="card"><h3>الخطوة ٢: تحرير المقاطع</h3><p>راجع النسخ المكتوبة. يمكنك:</p><ul>
<li><strong>تعديل النص</strong> — أصلح أي أخطاء في النسخ مباشرة من الجدول.</li>
<li><strong>الترجمة التلقائية</strong> — ترجم جميع الأسطر غير المقفلة إلى العربية باستخدام الذكاء الاصطناعي.</li>
<li><strong>إضافة التشكيل</strong> — أضف الحركات العربية إلى النص المترجم.</li>
<li><strong>كشف المشاعر</strong> — يستمع الذكاء الاصطناعي لنبرة صوت كل مقطع ويحدد أسلوب التحدث المناسب.</li>
<li><strong>إصلاح التوقيت التلقائي</strong> — أعد مزامنة توقيت المقاطع مع الصوت الأصلي بدقة.</li>
<li><strong>🔒 قفل</strong> — احمِ أسطراً محددة من التغيير بواسطة إصلاح التوقيت أو الترجمة أو التشكيل.</li></ul></div>
<div class="card"><h3>الخطوة ٣: إعداد استنساخ الأصوات</h3><p>يحلل التطبيق كمية الكلام المتوفرة لكل متحدث. المتحدثون الذين لديهم صوت كافٍ (≥ ثانية واحدة) يمكن استنساخ أصواتهم — ينسخ الذكاء الاصطناعي خصائص صوتهم الفريدة من الفيديو ليبدو الدبلج العربي مشابهاً لهم.</p></div>
<div class="card"><h3>الخطوة ٣.٥: اختيار المتحدثين للاستنساخ</h3><p>راجع إرشادات الجودة لكل متحدث. ألغِ تحديد أي متحدث لا تريد استنساخه. المتحدثون ذوو الصوت القليل جداً سيُنتجون نسخاً ضعيفة — استخدم صوتاً من مكتبة الاستوديو بدلاً من ذلك.</p></div>
<div class="card"><h3>الخطوة ٤: تعيين الأصوات</h3><p>اختر صوتاً لكل متحدث. يمكنك استخدام:</p><ul>
<li><strong>الأصوات المستنسخة</strong> — منسوخة من الفيديو الخاص بك (إذا نجح الاستنساخ).</li>
<li><strong>أصوات مكتبة الاستوديو</strong> — أصوات احترافية عالية الجودة (مُرقّمة، الأسماء مخفية).</li></ul>
<p>استخدم 🎲 تعيين تلقائي ليدع التطبيق يختار أفضل تطابق تلقائياً. لا يتشارك متحدثان نفس الصوت المُرقّم أبداً.</p></div>
<div class="card"><h3>الخطوة ٥: توليد الصوت العربي</h3><p>اضغط توليد الصوت العربي. ينطق الذكاء الاصطناعي كل سطر بالعربية باستخدام الصوت والمشاعر/الأسلوب المعين. قد يستغرق هذا ١–٣ دقائق حسب طول المقطع.</p></div>
<div class="card"><h3>الخطوة ٦: المراجعة والضبط الدقيق</h3><p>استمع للنتيجة. يمكنك:</p><ul>
<li>🔄 <strong>إعادة نطق سطر واحد</strong> — أعد توليد سطر واحد فقط دون إعادة المقطع كاملاً.</li>
<li>🎚️ <strong>ضبط الجدول الزمني</strong> — اسحب مربعات المقاطع يميناً/يساراً (بالمللي ثانية) لمحاذاة حركة الشفاه تماماً. لا يمكن للمربعات أن تتداخل.</li>
<li>🎬 <strong>دمج مع الفيديو</strong> — ادمج الصوت المدبلج مع الفيديو الأصلي والموسيقى الخلفية.</li>
<li>💾 <strong>حفظ / تحميل المشروع</strong> — احفظ عملك في أي وقت كملف JSON. حمّله لاحقاً لمتابعة التحرير دون إعادة نسخ.</li></ul></div>

<h2>الأسئلة الشائعة</h2>
<div class="card"><h3>ما هي صيغ الملفات المدعومة؟</h3><p>الصوت: MP3، WAV، FLAC، AAC، OGG. الفيديو: MP4، AVI، MKV، MOV، WEBM. الحد الأقصى للمدة: ٦٠ ثانية. الحد الأقصى لحجم الملف: ٤٠٠ ميغابايت.</p></div>
<div class="card"><h3>كيف يعمل استنساخ الأصوات؟</h3><p>يستخرج الذكاء الاصطناعي خصائص صوت كل متحدث من الصوت الأصلي وينشئ نسخة اصطناعية. تُستخدم هذه النسخة لنطق الترجمة العربية بصوت مشابه للمتحدث الأصلي. مطلوب ثانية واحدة على الأقل من كلام واضح؛ ١٠ ثوانٍ فأكثر تعطي أفضل النتائج.</p></div>
<div class="card"><h3>ما هي النقاط وكيف تُحسب التكاليف؟</h3><p>النقاط هي وحدة الفوترة الداخلية: نقطة واحدة = ٠.٠١ دولار أمريكي (١٠٠ نقطة = دولار واحد). الترجمة والتحليل تكلف بضعة سنتات لكل مقطع. توليد الصوت يُحسب بعدد الأحرف. كل زر يعرض تكلفته التقديرية قبل الضغط عليه، والاستخدام الفعلي يظهر في المربع الأخضر في الخطوة ١.</p></div>
<div class="card"><h3>ماذا يفعل القفل 🔒؟</h3><p>قفل سطر يحميه من ثلاث عمليات: إصلاح التوقيت التلقائي، الترجمة التلقائية، وإضافة التشكيل. الأسطر المقفلة تحتفظ بالنص والتوقيت والتنسيق الحالي دون تغيير. استخدم هذا للأسطر التي أتقنتها يدوياً.</p></div>
<div class="card"><h3>هل يمكنني دمج عدة وسوم للمشاعر/الأسلوب؟</h3><p>نعم! استخدم القائمة المنسدلة لإضافة الوسوم واحداً تلو الآخر، أو اكتب مباشرة في مربع النص (مثال: "واثق، هادئ"). فقط الوسوم المعتمدة من القائمة الرسمية مقبولة؛ الكلمات غير المعروفة تُحذف تلقائياً. أمثلة: "قلق، خائف"، "مرح، مازح"، "هادئ، حازم".</p></div>
<div class="card"><h3>لماذا يبدو صوتي المستنسخ آلياً؟</h3><p>هذا يعني عادةً أن المتحدث لم يكن لديه صوت كافٍ في المقطع الأصلي. تحقق من إرشادات الجودة في الخطوة ٣.٥ — إذا كانت تقول ⚠️ أو ، غيّر صوت هذا المتحدث إلى صوت مكتبة الاستوديو في الخطوة ٤ للحصول على نتائج أفضل.</p></div>
<div class="card"><h3>كيف يعمل الضبط الدقيق للجدول الزمني؟</h3><p>في الخطوة ٦، اضغط 🎚️ ضبط الجدول الزمني. كل مربع أزرق يمثل سطراً منطوقاً واحداً. اسحب المربعات يميناً أو يساراً لتغيير توقيتها بالمللي ثانية. مسطرة زمنية في الأعلى تظهر موقعك. تتوقف المربعات عند المقاطع المجاورة لمنع التداخل. اضغط ✅ تأكيد لإعادة بناء الصوت بتعديلاتك.</p></div>
<div class="card"><h3>هل يحتفظ الفيديو المدمج بالموسيقى الخلفية الأصلية؟</h3><p>نعم. يفصل التطبيق الأصوات عن الخلفية الصوتية أثناء المعالجة. عند الدمج، تُخلط الأصوات العربية المدبلجة مع الموسيقى/المؤثرات الخلفية الأصلية بمستويات صوت متوازنة.</p></div>
<div class="card"><h3>هل يمكنني حفظ تقدمي والعودة لاحقاً؟</h3><p>نعم. اضغط 💾 حفظ المشروع في الخطوة ٢ لتنزيل ملف JSON يحتوي على جميع المقاطع والترجمات وتعيينات الأصوات والإعدادات. لاحقاً، اضغط 📂 تحميل المشروع لاستعادة كل شيء كما تركته بالضبط.</p></div>
<div class="card"><h3>هل مزامنة حركة الشفاه متوفرة؟</h3><p>ليس بعد. مزامنة حركة الشفاه (مطابقة حركة الشفاه مع اللغة الجديدة) مخطط لها في تحديث مستقبلي. حالياً، ينتج التطبيق صوتاً مدبلجاً عالي الجودة يمكنك دمجه مع الفيديو الخاص بك.</p></div>
<div class="card"><h3>هل ستتأثر دقة أو جودة الفيديو؟</h3><p>لا. يتم نسخ مسار الفيديو الأصلي دون أي تغيير، ويُستبدل مسار الصوت فقط بالصوت العربي المدبلج (أو يُدمج معه). تبقى الدقة ومعدل البت ومعدل الإطارات وجودة الصورة كما هي في ملفك الأصلي.</p></div>
<div class="card"><h3>هل يتم حفظ ملفاتي ومقاطع الفيديو المولدة على خوادمكم؟</h3><p>لا. ملفاتك المرفوعة والنسخ الكتابية والصوت/الفيديو المُولّد تُخزّن مؤقتاً على الخادم فقط أثناء جلستك النشطة. تُحذف تلقائياً عند إعادة تشغيل الخادم أو بعد فترة قصيرة. يجب عليك تنزيل نتائجك أثناء جلستك — بمجرد إغلاق المتصفح أو إعادة تدوير الخادم، تختفي الملفات نهائياً. لا نحتفظ بنسخ من وسائطك. لسلامتك، اضغط دائماً على أزرار التنزيل في الخطوة ٦ قبل مغادرة الصفحة.</p></div>
</div>
</div>

<script>
function showLang(l){
  document.getElementById('secEn').style.display = (l === 'en') ? '' : 'none';
  document.getElementById('secAr').style.display = (l === 'ar') ? '' : 'none';
  document.getElementById('btnEn').className = (l === 'en') ? 'on' : '';
  document.getElementById('btnAr').className = (l === 'ar') ? 'on' : '';
}
</script>
</body>
</html>
'''

p.write_text(HTML, encoding="utf-8")
print("help.html rebuilt: single copy, unified cards, EN/AR toggle, resolution FAQ in both languages.")