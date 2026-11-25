"""Getting-started emails for new accounts that asked for them (1.108.0).

At sign-up the login page has an optional box "Send me tips for getting started". Only people who tick it are written
to, and only while they have not made anything yet: at most three emails, on day 1, day 3 and day 7 after sign-up.
Each email carries a one-click unsubscribe link; the Account page has the same switch. The admin page has the master
switch (off until Ali turns it on), a preview of every email and the counts.

Everything lives on the data volume (DATA_DIR/onboarding/): state.json (who asked, what was sent, who unsubscribed)
and secret.key (signs the unsubscribe links), so no database change is needed. Sending, "did this person make
something?" and the e-mail address come from main.py, so this module never calls a service itself. Nothing here raises.
"""
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from config import DATA_DIR

SITE = "https://lisanai.org"
LOGO = SITE + "/logo.png"
STEPS = (1, 3, 7)                 # days after sign-up
LATE_DAYS = 2                     # an email more than this many days late is skipped (no "day 1" email on day 6)
MAX_AGE_DAYS = 10                 # older accounts are never written to
_UID = re.compile(r"[0-9A-Za-z-]{8,64}")
_FOLDER = Path(DATA_DIR) / "onboarding"
_lock = threading.RLock()

GOLD, NAVY, INK, MUTED, BG = "#c9952b", "#1a237e", "#1f2937", "#6b7280", "#f5f6fb"


# ------------------------------------------------------------------ state

def _path(name):
    return _FOLDER / name


def _load():
    try:
        d = json.loads(_path("state.json").read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("users", {})
            d.setdefault("enabled", False)
            return d
    except Exception:
        pass
    return {"enabled": False, "users": {}}


def _save(d):
    try:
        _FOLDER.mkdir(parents=True, exist_ok=True)
        tmp = _path("state.json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _path("state.json"))
        return True
    except Exception as ex:
        print(f"[onboarding] could not save: {ex}")
        return False


def _ok_uid(uid):
    return isinstance(uid, str) and bool(_UID.fullmatch(uid))


def enabled():
    return bool(_load().get("enabled"))


def set_enabled(on):
    with _lock:
        d = _load()
        d["enabled"] = bool(on)
        return _save(d)


def opt_in(uid, created_ts=None, lang="en"):
    """The person ticked the box (at sign-up or later on the Account page). created_ts = when the account was made."""
    if not _ok_uid(uid):
        return False
    with _lock:
        d = _load()
        u = d["users"].get(uid) or {"sent": {}}
        u["opt_in"] = int(time.time())
        u.pop("off", None)
        if created_ts and not u.get("created"):
            u["created"] = int(created_ts)
        u.setdefault("created", int(time.time()))
        u["lang"] = "ar" if lang == "ar" else "en"
        d["users"][uid] = u
        return _save(d)


def opt_out(uid):
    """Unsubscribe: no more of these emails, ever, unless the person ticks the box again."""
    if not _ok_uid(uid):
        return False
    with _lock:
        d = _load()
        u = d["users"].get(uid) or {"sent": {}}
        u["off"] = int(time.time())
        d["users"][uid] = u
        return _save(d)


def wants(uid):
    u = _load()["users"].get(uid) if _ok_uid(uid) else None
    return bool(u and u.get("opt_in") and not u.get("off"))


def forget(uid):
    if not _ok_uid(uid):
        return
    with _lock:
        d = _load()
        if d["users"].pop(uid, None) is not None:
            _save(d)


def stats():
    d = _load()
    us = d["users"].values()
    return {"enabled": bool(d.get("enabled")),
            "opted_in": sum(1 for u in us if u.get("opt_in")),
            "unsubscribed": sum(1 for u in us if u.get("off")),
            "made_something": sum(1 for u in us if u.get("done")),
            "sent": {str(s): sum(1 for u in us if str(s) in (u.get("sent") or {})) for s in STEPS}}


# ------------------------------------------------------------------ unsubscribe links

def _secret():
    p = _path("secret.key")
    try:
        k = p.read_bytes()
        if len(k) >= 32:
            return k
    except Exception:
        pass
    with _lock:
        k = secrets.token_bytes(32)
        try:
            _FOLDER.mkdir(parents=True, exist_ok=True)
            p.write_bytes(k)
        except Exception:
            pass
        return k


def token(uid):
    return hmac.new(_secret(), ("unsub:" + str(uid)).encode(), hashlib.sha256).hexdigest()[:32]


def check_token(uid, tok):
    return _ok_uid(uid) and isinstance(tok, str) and hmac.compare_digest(token(uid), tok)


def unsubscribe_url(uid):
    return f"{SITE}/unsubscribe?u={uid}&t={token(uid)}"


# ------------------------------------------------------------------ who gets what now

def due(now=None):
    """[(uid, step)] to send this round: asked for it, not unsubscribed, nothing made yet, the day has come."""
    now = now or time.time()
    d = _load()
    out = []
    if not d.get("enabled"):
        return out
    for uid, u in d["users"].items():
        if not u.get("opt_in") or u.get("off") or u.get("done"):
            continue
        age = (now - float(u.get("created") or now)) / 86400.0
        if age > MAX_AGE_DAYS:
            continue
        sent = u.get("sent") or {}
        for step in STEPS:
            if str(step) in sent:
                continue
            if step <= age <= step + LATE_DAYS:
                out.append((uid, step))
            break                       # one at a time, in order
    return out


def mark(uid, step=None, done=False):
    with _lock:
        d = _load()
        u = d["users"].get(uid)
        if not u:
            return
        if done:
            u["done"] = int(time.time())
        if step is not None:
            u.setdefault("sent", {})[str(step)] = int(time.time())
        _save(d)


def run(send, made_something, email_for, credits_for=None, now=None):
    """One round. send(to, subject, html, text, unsubscribe_url) -> bool; made_something(uid) -> True/False/None (None:
    could not check, try again later); email_for(uid) -> address or None; credits_for(uid) -> balance or None."""
    counts = {"sent": 0, "skipped": 0, "failed": 0}
    for uid, step in due(now):
        try:
            made = made_something(uid)
            if made is None:
                counts["skipped"] += 1
                continue
            if made:
                mark(uid, done=True)
                counts["skipped"] += 1
                continue
            to = email_for(uid)
            if not to:
                counts["skipped"] += 1
                continue
            u = _load()["users"].get(uid) or {}
            bal = credits_for(uid) if credits_for else None
            subject, body_html, body_text = render(step, u.get("lang", "en"), uid, bal)
            if send(to, subject, body_html, body_text, unsubscribe_url(uid)):
                mark(uid, step)
                counts["sent"] += 1
            else:
                counts["failed"] += 1
        except Exception as ex:
            print(f"[onboarding] {uid} step {step}: {ex}")
            counts["failed"] += 1
    return counts


# ------------------------------------------------------------------ the emails

_LANGS_EN = "English, Spanish, French, Portuguese, Italian, German, Dutch or Polish"
_LANGS_AR = "الإنجليزية أو الإسبانية أو الفرنسية أو البرتغالية أو الإيطالية أو الألمانية أو الهولندية أو البولندية"


def _content(step, credits):
    """{en: {...}, ar: {...}} for one email: subject, preheader, title, paragraphs, steps, buttons, note."""
    have_en = f"You have <b>{credits} free credits</b> waiting in your account." if credits else "Your free starting credits are waiting in your account."
    have_ar = f"لديك <b>{credits} رصيد مجاني</b> بانتظارك في حسابك." if credits else "رصيدك المجاني للبدء بانتظارك في حسابك."
    if step == 1:
        return {
            "en": {"subject": "Your first Arabic dub in 3 steps",
                   "pre": "Upload a short video and hear it in Arabic in a few minutes.",
                   "title": "Welcome to Lisan AI",
                   "paras": ["Lisan AI turns your video into natural Arabic speech, with full tashkeel and the original background sound kept.",
                             have_en],
                   "steps": [("Upload a short video", f"Start with a clip of up to 30 seconds. The speech should be in {_LANGS_EN}."),
                             ("Check the Arabic", "Read the translation and change any word you like. It's free."),
                             ("Download", "Get your dubbed video, the Arabic subtitles or the audio.")],
                   "buttons": [("Start my first dub", SITE + "/app"), ("Watch the tutorial", SITE + "/static/Lisan-Ai-Long-Dub-Tutorial-En.mp4")],
                   "note": "Every button in the app shows its price before you click, so nothing is charged by surprise."},
            "ar": {"subject": "أول دبلجة عربية لك في 3 خطوات",
                   "pre": "ارفع فيديو قصيراً واستمع إليه بالعربية خلال دقائق.",
                   "title": "مرحباً بك في Lisan AI",
                   "paras": ["يحوّل Lisan AI الفيديو الخاص بك إلى كلام عربي طبيعي، بتشكيل كامل ومع الحفاظ على الصوت الخلفي الأصلي.",
                             have_ar],
                   "steps": [("ارفع فيديو قصيراً", f"ابدأ بمقطع حتى 30 ثانية. يجب أن يكون الكلام فيه باللغة {_LANGS_AR}."),
                             ("راجع النص العربي", "اقرأ الترجمة وغيّر أي كلمة تريدها، مجاناً."),
                             ("نزّل النتيجة", "احصل على الفيديو المدبلج أو الترجمة العربية أو الصوت.")],
                   "buttons": [("ابدأ دبلجتي الأولى", SITE + "/app"), ("شاهد الشرح", SITE + "/static/Lisan-Ai-Long-Dub-Tutorial-Ar.mp4")],
                   "note": "يعرض كل زر في التطبيق سعره قبل أن تضغط عليه، فلا يُخصم شيء دون علمك."},
        }
    if step == 3:
        return {
            "en": {"subject": "Your free credits are waiting",
                   "pre": "A 30-second clip is enough to hear Lisan AI in action.",
                   "title": "Try it with one short clip",
                   "paras": [have_en, "A 30-second clip is enough to hear how your video sounds in Arabic. Here is what people dub with Lisan AI:"],
                   "steps": [("YouTube videos", "Reach more than 400 million Arabic speakers."),
                             ("Courses and lessons", "Explain your topic in clear Modern Standard Arabic."),
                             ("A text instead of a video?", "Use “From text” to turn a script into Arabic speech.")],
                   "buttons": [("Dub a short clip", SITE + "/app"), ("Turn a text into speech", SITE + "/text-voice")],
                   "note": "Longer videos, up to 60 minutes, go through “Long dub”."},
            "ar": {"subject": "رصيدك المجاني بانتظارك",
                   "pre": "يكفي مقطع مدته 30 ثانية لتسمع Lisan AI بنفسك.",
                   "title": "جرّبه بمقطع قصير واحد",
                   "paras": [have_ar, "يكفي مقطع مدته 30 ثانية لتسمع كيف يبدو الفيديو الخاص بك بالعربية. هذا ما يدبلجه الناس بـ Lisan AI:"],
                   "steps": [("فيديوهات يوتيوب", "للوصول إلى أكثر من 400 مليون متحدث بالعربية."),
                             ("الدورات والدروس", "اشرح موضوعك بعربية فصحى واضحة."),
                             ("لديك نص بدلاً من فيديو؟", "استخدم «من نص» لتحويل النص إلى كلام عربي.")],
                   "buttons": [("دبلج مقطعاً قصيراً", SITE + "/app"), ("حوّل نصاً إلى كلام", SITE + "/text-voice")],
                   "note": "أما الفيديوهات الأطول، حتى 60 دقيقة، فتمرّ عبر «دبلجة طويلة»."},
        }
    return {
        "en": {"subject": "Can we help you get started?",
               "pre": "Answers to the three questions new users ask most.",
               "title": "Can we help?",
               "paras": ["You signed up a week ago but haven't made a dub yet. Maybe one of these answers helps:"],
               "steps": [("My video is already in Arabic", f"Lisan AI dubs <i>into</i> Arabic. The speech in your video should be in {_LANGS_EN}."),
                         ("My video is longer than 30 seconds", "Use “Long dub” for videos of up to 60 minutes. We email you when it's ready."),
                         ("The upload seems slow", "Keep the page open. The bar shows how many MB are sent and how long is left.")],
               "buttons": [("Open Lisan AI", SITE + "/app"), ("Read the help page", SITE + "/help")],
               "note": "Something else? Simply reply to this email, or ask the assistant in the app (the chat button at the bottom)."},
        "ar": {"subject": "هل يمكننا مساعدتك في البدء؟",
               "pre": "إجابات عن أكثر ثلاثة أسئلة يطرحها المستخدمون الجدد.",
               "title": "هل يمكننا مساعدتك؟",
               "paras": ["سجّلت قبل أسبوع لكنك لم تصنع دبلجة بعد. ربما تساعدك إحدى هذه الإجابات:"],
               "steps": [("الفيديو الخاص بي بالعربية أصلاً", f"يدبلج Lisan AI <i>إلى</i> العربية. يجب أن يكون الكلام في الفيديو باللغة {_LANGS_AR}."),
                         ("الفيديو أطول من 30 ثانية", "استخدم «دبلجة طويلة» للفيديوهات حتى 60 دقيقة، ونرسل إليك بريداً عندما تجهز."),
                         ("الرفع يبدو بطيئاً", "أبقِ الصفحة مفتوحة. يعرض الشريط عدد الميغابايت المرسلة والوقت المتبقي.")],
               "buttons": [("افتح Lisan AI", SITE + "/app"), ("اقرأ صفحة المساعدة", SITE + "/help")],
               "note": "سؤال آخر؟ ما عليك إلا الرد على هذا البريد، أو اسأل المساعد في التطبيق (زر المحادثة في الأسفل)."},
    }


def _block(c, ar):
    """One language as email-safe HTML (tables, inline styles)."""
    d = 'dir="rtl" align="right"' if ar else 'dir="ltr" align="left"'
    ta = "right" if ar else "left"
    font = "'Segoe UI',Tahoma,Arial,sans-serif" if ar else "'Segoe UI',Helvetica,Arial,sans-serif"
    h = [f'<tr><td {d} style="padding:28px 32px 8px;font-family:{font};color:{INK};text-align:{ta};">',
         f'<h1 style="margin:0 0 14px;font-size:22px;line-height:1.35;color:{NAVY};">{html.escape(c["title"])}</h1>']
    for p in c["paras"]:
        h.append(f'<p style="margin:0 0 12px;font-size:15px;line-height:1.7;">{p}</p>')
    h.append(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:8px 0 6px;" dir="{'rtl' if ar else 'ltr'}">')
    for i, (head, text) in enumerate(c["steps"], 1):
        num = f'<td width="34" valign="top" style="padding:6px 0;"><div style="width:26px;height:26px;border-radius:13px;background:{GOLD};color:#fff;font-weight:700;font-size:14px;line-height:26px;text-align:center;">{i}</div></td>'
        txt = (f'<td valign="top" style="padding:6px 8px;font-size:14px;line-height:1.6;text-align:{ta};">'
               f'<b style="color:{NAVY};">{html.escape(head)}</b><br>{text}</td>')
        h.append(f'<tr>{num + txt}</tr>')          # the block's own direction puts the number first: right in Arabic, left in English
    h.append('</table>')
    (b1, u1), (b2, u2) = c["buttons"]
    h.append(f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:18px 0 8px;" dir="{'rtl' if ar else 'ltr'}"><tr>'
             f'<td style="border-radius:8px;background:{NAVY};"><a href="{u1}" style="display:inline-block;padding:12px 22px;font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;font-family:{font};">{html.escape(b1)}</a></td>'
             f'<td width="10"></td>'
             f'<td style="border-radius:8px;border:2px solid {GOLD};"><a href="{u2}" style="display:inline-block;padding:10px 18px;font-size:14px;font-weight:700;color:{NAVY};text-decoration:none;font-family:{font};">{html.escape(b2)}</a></td>'
             f'</tr></table>')
    h.append(f'<p style="margin:10px 0 0;font-size:13px;line-height:1.6;color:{MUTED};">{html.escape(c["note"])}</p></td></tr>')
    return "".join(h)


def render(step, lang="en", uid="", credits=None):
    """(subject, html, text) of email `step` (1, 3 or 7). Both languages, the person's own language first."""
    try:
        credits = int(credits) if credits is not None and int(credits) > 0 else None
    except (TypeError, ValueError):
        credits = None
    c = _content(step, credits)
    first, second = ("ar", "en") if lang == "ar" else ("en", "ar")
    subject = c[first]["subject"]
    unsub = unsubscribe_url(uid) if uid else SITE + "/account"
    pre = html.escape(c[first]["pre"])
    sep = f'<tr><td style="padding:14px 32px 0;"><div style="border-top:1px solid #e5e7eb;"></div></td></tr>'
    foot = (f'<tr><td align="center" style="padding:22px 32px 28px;font-family:Arial,sans-serif;font-size:12px;line-height:1.6;color:{MUTED};text-align:center;">'
            f'You get this email because you asked for getting-started tips when you signed up. At most 3 emails.<br>'
            f'<span dir="rtl">تصلك هذه الرسالة لأنك طلبت نصائح للبدء عند التسجيل، ولن تصلك أكثر من 3 رسائل.</span><br>'
            f'<a href="{unsub}" style="color:{MUTED};text-decoration:underline;">Unsubscribe · إلغاء الاشتراك</a> &nbsp;·&nbsp; '
            f'<a href="{SITE}/imprint" style="color:{MUTED};text-decoration:underline;">Imprint · بيانات الناشر</a> &nbsp;·&nbsp; '
            f'<a href="{SITE}/privacy" style="color:{MUTED};text-decoration:underline;">Privacy · الخصوصية</a><br>Lisan AI · Gronau (Leine), Germany</td></tr>')
    body = (f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(subject)}</title></head><body style="margin:0;padding:0;background:{BG};">'
            f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{pre}</div>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};"><tr><td align="center" style="padding:24px 12px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:580px;background:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 2px 10px rgba(26,35,126,.08);">'
            f'<tr><td align="center" style="background:{NAVY};padding:22px 24px;">'
            f'<a href="{SITE}" style="text-decoration:none;"><img src="{LOGO}" width="54" height="54" alt="Lisan AI" style="display:block;margin:0 auto 8px;border:0;border-radius:12px;background:#ffffff;">'
            f'<span style="font-family:Arial,sans-serif;font-size:20px;font-weight:700;color:#ffffff;letter-spacing:.3px;">Lisan <span style="color:{GOLD};">AI</span></span></a></td></tr>'
            + _block(c[first], first == "ar") + sep + _block(c[second], second == "ar") + sep + foot +
            '</table></td></tr></table></body></html>')

    def plain(cc):
        out = [cc["title"], ""] + [re.sub(r"<[^>]+>", "", p) for p in cc["paras"]] + [""]
        out += [f"{i}. {hd}: {re.sub(r'<[^>]+>', '', tx)}" for i, (hd, tx) in enumerate(cc["steps"], 1)]
        out += ["", f"{cc['buttons'][0][0]}: {cc['buttons'][0][1]}", f"{cc['buttons'][1][0]}: {cc['buttons'][1][1]}", "", cc["note"]]
        return "\n".join(out)
    text = plain(c[first]) + "\n\n-----\n\n" + plain(c[second]) + f"\n\n-----\nUnsubscribe / إلغاء الاشتراك: {unsub}\nLisan AI · {SITE}/imprint"
    return subject, body, text
