"""Feedback and invite links (Ali, Nov 2026).

Feedback: a user who uses the app a lot (see _engaged in main.py) is asked once, right after downloading a finished result,
"How do you like Lisan AI?" (1 to 5 stars) and "What should we improve?" (optional). Closing the box without an answer asks
again after 7 days, at most twice in all. An answer is never asked for again. Ali reads the answers in admin, Activity tab.

Invite link: after a good answer (4 or 5 stars) the thank-you shows the user's own link, lisanai.org/?ref=CODE. When a friend
creates a new account with it, the user gets 50 credits as a thank-you, for at most 10 friends. Each gift has a fixed
operation id (from the friend's account id), so it is given at most once even when it is tried again.

Everything is kept in one small file on the data volume (feedback.json), backed up off-site with the other register files
and restored from there if the volume lost it. No AI, no extra database table."""
import json
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

from config import DATA_DIR

FILE = Path(DATA_DIR) / "feedback.json"
SITE = "https://lisanai.org"
GIFT_CREDITS = 50
MAX_GIFTS = 10
ASK_AGAIN_DAYS = 7
MAX_ASKS = 2
NEW_ACCOUNT_HOURS = 72          # only an account made in the last 3 days can be counted as a friend who signed up with a link
TEXT_MAX = 2000
_CODE = re.compile(r"[a-z0-9]{6,16}")
_lock = threading.RLock()


def _empty():
    return {"users": {}, "codes": {}, "invites": {}}


def _sound(data):
    return isinstance(data, dict) and all(isinstance(data.get(k), dict) for k in ("users", "codes", "invites"))


def _write(data):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_name(FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, FILE)


def _restore():
    """The data volume lost the file: the newest off-site copy when there is one (and it is sound), else None."""
    try:
        import r2_backup
        raw = r2_backup.restore_register_file(FILE.name)
        data = json.loads(raw.decode("utf-8")) if raw else None
        if _sound(data):
            _write(data)
            print("[feedback] the file was missing; restored it from the off-site backup")
            return data
    except Exception as ex:
        print(f"[feedback] restore failed: {ex}")
    return None


def _read():
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
        if _sound(data):
            return data
        raise ValueError("not a feedback file")
    except FileNotFoundError:
        return _restore() or _empty()
    except Exception as ex:
        # never overwrite a damaged file: keep it aside for a look, then start again from the off-site copy
        print(f"[feedback] the file could not be read ({ex}); kept it aside")
        try:
            os.replace(FILE, FILE.with_name(f"{FILE.name}.damaged-{int(time.time())}"))
        except OSError:
            pass
        return _restore() or _empty()


def _user(data, uid):
    return data["users"].setdefault(str(uid), {})


# ------------------------------------------------------------------ feedback

def should_ask(uid, now=None):
    """True when this user may be shown the question now (whether they use the app enough is decided by the caller)."""
    if not uid:
        return False
    now = time.time() if now is None else now
    with _lock:
        u = _read()["users"].get(str(uid)) or {}
    asks = u.get("asks") or []
    if u.get("answered") or len(asks) >= MAX_ASKS:
        return False
    return not asks or now - float(asks[-1]) >= ASK_AGAIN_DAYS * 86400


def note_asked(uid, now=None):
    with _lock:
        data = _read()
        u = _user(data, uid)
        u["asks"] = (u.get("asks") or []) + [time.time() if now is None else now]
        _write(data)


def answer(uid, stars, text="", page="", now=None):
    """(True, None) or (False, message). Saves the answer once; a second answer (another tab) changes nothing."""
    try:
        stars = 0 if isinstance(stars, bool) else int(stars)
    except (TypeError, ValueError):
        stars = 0
    if not 1 <= stars <= 5:
        return False, "Please choose from 1 to 5 stars."
    text = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    if len(text) > TEXT_MAX:
        return False, f"Please keep it under {TEXT_MAX} characters."
    page = re.sub(r"[^a-z_-]", "", str(page or "").lower())[:20]
    with _lock:
        data = _read()
        u = _user(data, uid)
        if not u.get("answered"):
            u.update(answered=time.time() if now is None else now, stars=stars, text=text, page=page)
            _write(data)
    return True, None


# ------------------------------------------------------------------ the sign-up signal for Google Ads (1.97.6, Ali)

SIGNUP_HOURS = 72          # only an account made in the last 3 days is reported as a new sign-up


def signup_signal(uid, account_created, now=None):
    """True ONCE for a new account (made in the last SIGNUP_HOURS): the page then tells Google Analytics "sign_up", which Google Ads
    counts as the goal of an ad. Every later call, and any older account, gives False."""
    if not uid or account_created is None:
        return False
    now = time.time() if now is None else now
    try:
        if now - float(account_created) > SIGNUP_HOURS * 3600:
            return False
    except (TypeError, ValueError):
        return False
    with _lock:
        data = _read()
        u = _user(data, uid)
        if u.get("signup_sent"):
            return False
        u["signup_sent"] = now
        _write(data)
    return True


# ------------------------------------------------------------------ invite links

def invite_code(uid):
    """This user's own code (made the first time it is needed, then always the same)."""
    with _lock:
        data = _read()
        u = _user(data, uid)
        if u.get("code") and data["codes"].get(u["code"]) == str(uid):
            return u["code"]
        while True:
            code = secrets.token_hex(4)
            if code not in data["codes"]:
                break
        data["codes"][code] = str(uid)
        u["code"] = code
        _write(data)
        return code


def invite_link(uid):
    return f"{SITE}/?ref={invite_code(uid)}"


def gift_operation(friend_uid):
    """The fixed operation id of the thank-you for this friend: the same on every try, so it is given at most once."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "lisanai.org/invite/" + str(friend_uid)))


def _gifts_of(data, uid):
    return sum(1 for v in data["invites"].values() if v.get("by") == str(uid) and v.get("gift") in ("given", "pending", "failed"))


def invite_status(uid):
    with _lock:
        data = _read()
    friends = [v for v in data["invites"].values() if v.get("by") == str(uid)]
    given = sum(1 for v in friends if v.get("gift") == "given")
    return {"friends": len(friends), "gifts": given, "left": max(0, MAX_GIFTS - _gifts_of(data, uid)),
            "credits": GIFT_CREDITS, "max": MAX_GIFTS}


def _give(friend_uid, give):
    """Tries the gift of one recorded friend. give(referrer, credits, operation_id, reason) -> True when the credits were added."""
    with _lock:
        rec = dict(_read()["invites"].get(str(friend_uid)) or {})
    if rec.get("gift") not in ("pending", "failed"):
        return rec.get("gift")
    try:
        ok = bool(give(rec["by"], GIFT_CREDITS, rec["op"], f"Invite thank-you: friend {friend_uid} signed up with the invite link"))
    except Exception as ex:
        print(f"[invite] gift for friend {friend_uid} failed: {ex}")
        ok = False
    with _lock:
        data = _read()
        cur = data["invites"].get(str(friend_uid))
        if cur:
            cur["gift"] = "given" if ok else "failed"
            cur["tries"] = int(cur.get("tries") or 0) + 1
            if ok:
                cur["given_at"] = time.time()
            _write(data)
    return "given" if ok else "failed"


def claim(friend_uid, code, account_created, give, now=None):
    """A logged-in person who arrived with an invite link. Returns what happened: "given" (the friend's thank-you was added),
    "limit" (counted, but the user already had 10 gifts), "failed" (counted; the gift is tried again later), or why it was
    not counted: "bad_code", "self", "already", "old_account"."""
    now = time.time() if now is None else now
    code = str(code or "").strip().lower()
    if not friend_uid or not _CODE.fullmatch(code):
        return "bad_code"
    with _lock:
        data = _read()
        by = data["codes"].get(code)
        if not by:
            return "bad_code"
        if by == str(friend_uid):
            return "self"
        if str(friend_uid) in data["invites"]:
            return "already"
        if account_created is None or now - float(account_created) > NEW_ACCOUNT_HOURS * 3600:
            return "old_account"
        state = "pending" if _gifts_of(data, by) < MAX_GIFTS else "limit"
        data["invites"][str(friend_uid)] = {"by": by, "at": now, "gift": state, "op": gift_operation(friend_uid)}
        _write(data)
    if state == "limit":
        return "limit"
    return _give(friend_uid, give)


def retry_gifts(give, now=None):
    """Tries again every gift that could not be added before (for example while the database was briefly away)."""
    now = time.time() if now is None else now
    with _lock:
        waiting = [f for f, v in _read()["invites"].items()
                   if v.get("gift") in ("pending", "failed") and now - float(v.get("at") or 0) > 60 and int(v.get("tries") or 0) < 20]
    return sum(1 for f in waiting if _give(f, give) == "given")


# ------------------------------------------------------------------ admin and account deletion

def for_admin():
    """{"users": {uid: {stars, text, answered, asks, friends, gifts}}, "answers": [newest first], "invites": [newest first]}."""
    with _lock:
        data = _read()
    users = {}
    for uid, u in data["users"].items():
        users[uid] = {"stars": u.get("stars"), "text": u.get("text") or "", "answered": u.get("answered"), "page": u.get("page") or "",
                      "asks": len(u.get("asks") or []), "friends": 0, "gifts": 0}
    for friend, v in data["invites"].items():
        r = users.setdefault(v.get("by") or "", {"stars": None, "text": "", "answered": None, "page": "", "asks": 0, "friends": 0, "gifts": 0})
        r["friends"] += 1
        r["gifts"] += 1 if v.get("gift") == "given" else 0
    answers = sorted(({"uid": uid, **{k: u.get(k) for k in ("stars", "text", "answered", "page")}}
                      for uid, u in data["users"].items() if u.get("answered")), key=lambda a: -float(a["answered"] or 0))
    invites = sorted(({"friend": f, "by": v.get("by"), "at": v.get("at"), "gift": v.get("gift")} for f, v in data["invites"].items()),
                     key=lambda a: -float(a["at"] or 0))
    return {"users": users, "answers": answers, "invites": invites}


def forget(uid):
    """The account is deleted: its answer, its code and its own invite record go; friends it brought stay counted without it."""
    uid = str(uid)
    with _lock:
        data = _read()
        u = data["users"].pop(uid, None) or {}
        for code in [c for c, owner in data["codes"].items() if owner == uid] + ([u["code"]] if u.get("code") else []):
            data["codes"].pop(code, None)
        data["invites"].pop(uid, None)
        for v in data["invites"].values():
            if v.get("by") == uid:
                v["by"] = "deleted account"
        _write(data)
