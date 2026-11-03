"""Guides (Ali, Nov 2026): short articles in English and Arabic on lisanai.org/guides, so people searching for "dub a video
into Arabic" or "Arabic voiceover" find Lisan AI. Each article is guides/<slug>.html (an English and an Arabic part, shown by
the language switch of site.js like the help page); this module adds the page around it and the list of all guides.
Public pages (no login), listed in the sitemap. Pure text: no AI and no database."""
import html
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
FOLDER = HERE / "guides"
SITE = "https://lisanai.org"

GUIDES = (
    {"slug": "dub-youtube-video-into-arabic", "date": "2026-11-04",
     "en": "How to dub a YouTube video into Arabic", "ar": "كيف تدبلج فيديو يوتيوب إلى العربية",
     "den": "Step by step: an Arabic version of your YouTube video in your own voice, added to the same video as an Arabic audio track.",
     "dar": "خطوة بخطوة: نسخة عربية من فيديو يوتيوب بصوتك أنت، تضيفها إلى الفيديو نفسه مسارًا صوتيًا عربيًا."},
    {"slug": "arabic-voiceover-from-text", "date": "2026-11-04",
     "en": "Arabic voiceover from a script", "ar": "تعليق صوتي عربي من نص مكتوب",
     "den": "Paste a text in any supported language, or in Arabic, and get a natural Arabic voiceover as one MP3, in a voice you choose or your own.",
     "dar": "الصق نصًا بأي لغة مدعومة أو بالعربية، لتحصل على تعليق صوتي عربي طبيعي في ملف MP3 واحد، بصوت تختاره أو بصوتك أنت."},
    {"slug": "what-makes-a-good-arabic-dub", "date": "2026-11-04",
     "en": "What makes a good Arabic dub", "ar": "ما الذي يصنع دبلجة عربية جيدة؟",
     "den": "Modern Standard Arabic, full diacritics, the right male and female forms, timing and the speaker's own voice: what decides whether an Arabic dub sounds native.",
     "dar": "الفصحى، والتشكيل الكامل، وصيغ المذكر والمؤنث الصحيحة، والتوقيت، وصوت المتحدث نفسه: ما الذي يجعل الدبلجة العربية تبدو طبيعية."},
)
_BY_SLUG = {g["slug"]: g for g in GUIDES}


def paths():
    """The public paths, for the sitemap."""
    return ["/guides"] + ["/guides/" + g["slug"] for g in GUIDES]


def _e(s):
    return html.escape(str(s), quote=True)


def _page(title_en, title_ar, desc_en, path, body, ld=None, back=("/guides", "All guides", "كل الأدلة")):
    back, back_en, back_ar = back
    ld_tag = ('<script type="application/ld+json">' + json.dumps(ld, ensure_ascii=False).replace("</", "<\\/") + "</script>\n") if ld else ""
    return f"""<!DOCTYPE html>
<html lang="en" dir="ltr" data-lang="en" data-title-en="{_e(title_en)}" data-title-ar="{_e(title_ar)}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_e(title_en)}</title>
<meta name="description" content="{_e(desc_en)}">
<link rel="canonical" href="{SITE}{path}">
<meta property="og:type" content="article">
<meta property="og:title" content="{_e(title_en)}">
<meta property="og:description" content="{_e(desc_en)}">
<meta property="og:url" content="{SITE}{path}">
<meta property="og:image" content="{SITE}/logo.png">
<link rel="icon" type="image/png" href="/logo.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/site.css">
<script src="/site.js"></script>
<meta name="color-scheme" content="dark light">
<script src="/pwa.js" defer></script>
{ld_tag}<style>
body.site .guide-list a.card{{display:block;text-decoration:none}}
body.site .guide-list a.card:hover{{border-color:var(--line2)}}
body.site .guide-list .card p{{margin:0}}
body.site .page ol li,body.site .page ul li{{margin-bottom:8px}}
</style>
</head>
<body class="site">
<nav class="site-nav"><div class="in"><a class="site-brand" href="/"><img src="/logo.png" alt="">Lisan AI</a><a class="back" href="{back}"><span class="en">&#8592; {back_en}</span><span class="ar">{back_ar}</span></a></div></nav>
<div class="page">
{body}
</div>
<script src="/chat.js"></script>
</body>
</html>
"""


def index_html():
    cards = "".join(
        f'<a class="card" href="/guides/{_e(g["slug"])}"><h3><span class="en">{_e(g["en"])}</span><span class="ar">{_e(g["ar"])}</span></h3>'
        f'<p><span class="en">{_e(g["den"])}</span><span class="ar">{_e(g["dar"])}</span></p></a>\n' for g in GUIDES)
    body = ('<h1><span class="en">Guides</span><span class="ar">أدلة ومقالات</span></h1>\n'
            '<p class="sub"><span class="en">How to dub videos into Arabic and make Arabic voiceovers, step by step.</span>'
            '<span class="ar">كيف تدبلج الفيديوهات إلى العربية وتصنع تعليقًا صوتيًا عربيًا، خطوة بخطوة.</span></p>\n'
            '<div class="guide-list">\n' + cards + '</div>\n'
            '<p><a href="/"><span class="en">Lisan AI home</span><span class="ar">الصفحة الرئيسية</span></a> · '
            '<a href="/help"><span class="en">Help &amp; FAQ</span><span class="ar">المساعدة والأسئلة الشائعة</span></a></p>')
    return _page("Guides — Lisan AI", "أدلة ومقالات — Lisan AI",
                 "Guides to dubbing videos into Arabic and making Arabic voiceovers with Lisan AI.", "/guides", body,
                 back=("/", "Back to Home", "العودة إلى الصفحة الرئيسية"))


def guide_html(slug):
    """The page of one guide, or None when there is no such guide."""
    g = _BY_SLUG.get(str(slug or ""))
    if not g or not re.fullmatch(r"[a-z0-9-]{1,80}", g["slug"]):
        return None
    try:
        content = (FOLDER / (g["slug"] + ".html")).read_text(encoding="utf-8")
    except OSError:
        return None
    others = "".join(f'<li><a href="/guides/{_e(o["slug"])}"><span class="en">{_e(o["en"])}</span><span class="ar">{_e(o["ar"])}</span></a></li>'
                     for o in GUIDES if o["slug"] != g["slug"])
    body = (f'<h1><span class="en">{_e(g["en"])}</span><span class="ar">{_e(g["ar"])}</span></h1>\n' + content +
            '\n<h2><span class="en">More guides</span><span class="ar">أدلة أخرى</span></h2>\n<ul>' + others + '</ul>')
    ld = {"@context": "https://schema.org", "@type": "Article", "headline": g["en"], "alternativeHeadline": g["ar"],
          "description": g["den"], "inLanguage": ["en", "ar"], "datePublished": g["date"], "dateModified": g["date"],
          "author": {"@type": "Organization", "name": "Lisan AI"}, "publisher": {"@type": "Organization", "name": "Lisan AI",
          "logo": {"@type": "ImageObject", "url": SITE + "/logo.png"}}, "mainEntityOfPage": SITE + "/guides/" + g["slug"]}
    return _page(g["en"] + " — Lisan AI", g["ar"] + " — Lisan AI", g["den"], "/guides/" + g["slug"], body, ld)
