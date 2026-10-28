"""Second listener: Gemini listens to the whole voice track once and writes down what it hears.

The speech recognition on our server (Whisper) stays the base: its words and their exact times build the lines. The second listener
only fills in what Whisper got wrong:
- a sentence Whisper did not write down at all (it heard nothing between two of its words) becomes a new line;
- a line where Whisper heard something clearly different (it made up words in noise) gets the second listener's words.
Both are marked "check text" in the editor, with the other version kept for the person to compare.
Small differences (spelling, numbers written differently, a word here or there) are left as Whisper wrote them.

Measured on the 8-minute test clip (Oct 2026): Gemini 4 Argon wrote down the sentences Whisper missed or garbled; its own timing is
too loose to build lines from (11 of 129 lines more than 1 s off), so new lines are placed in the gap Whisper's word times leave.
"""
import base64
import difflib
import hashlib
import json
import os
import re
import time
from pathlib import Path

ENABLED = os.environ.get("LONGDUB_SECOND_LISTENER", "1").strip().lower() not in ("0", "off", "no", "false")
STATE_FILE = "speech_second.json"
STATE_VERSION = 1

ADD_MIN_TOKENS = 3        # a missed stretch needs at least this many words to become a line (when the voices cannot be read) ...
ADD_MIN_TOKENS_HEARD = 1  # ... and only this many when the separated voices show speech in that pause (real run, 28 Oct: "He's alive!"
                          # and "Oh, Christ." were two words each and were left out)
VOICE_MIN_SEC = 0.12      # a stretch of voice shorter than this is a click or a breath
VOICE_WINDOW = 2.0        # a sentence is placed on voice at most this far from where the second listener heard it
VOICE_JOIN_GAP = 0.6      # stretches of voice closer than this belong to the same sentence
UNHEARD_NEAR = 6.0        # a line none of whose words the second listener heard, with nothing it heard within this many seconds,
                          # is marked "maybe not speech" (Whisper makes up words in noise: real run, 28 Oct, 4:21 "I was the team.")
ADD_MIN_GAP = 0.5         # ... and Whisper must have heard nothing for at least this long there
SEC_PER_TOKEN = 0.33      # rough speaking speed, to size a new line when the second listener's own time is off
CHANGE_MIN_TOKENS = 3     # a line is rewritten only when both versions have at least this many words ...
CHANGE_MAX_SIMILAR = 0.5  # ... and they are less alike than this (0 = nothing in common, 1 = the same words)
PEEL_MARGIN = 1.0         # a sentence heard this long before / after Whisper's words is its own line, not part of the rewrite
TIME_SLACK = 3.0          # a rewrite needs the two versions heard within this many seconds of each other
CHANGE_ROW_MIN_TOKENS = 4  # a line Whisper heard is only rewritten when it has at least this many words ...
CHANGE_ROW_SHARE = 0.6     # ... and at least this share of them was heard differently (real run, 28 Oct: rewriting the different
                           # end of a short line split one sentence over two lines: "You would not Try" + "me, sweetheart.")

_TOKEN = re.compile(r"[^\W_]+(?:'[^\W_]+)?", re.UNICODE)


def tokens(text):
    return [t.lower() for t in _TOKEN.findall(str(text or ""))]


def prompt_for(language_name="English"):
    return (f"Transcribe this recording of a film's dialogue word for word, in {language_name}, exactly as spoken. "
            "Include every spoken word, also very short replies (\"Mm-hmm\", \"Yeah\", \"Bye\") and quiet speech, and do not add anything that is not said. "
            "Make one row per sentence (a new row also whenever another person starts speaking), with the start and end time in seconds from the "
            "beginning of the recording (decimals, e.g. 83.4). Return JSON only: {\"lines\": [{\"start\": 0.0, \"end\": 0.0, \"text\": \"...\"}]}")


def parse(text):
    """[{start, end, text}] from the answer, or None. Accepts {"lines": [...]} or a bare list."""
    try:
        t = str(text or "").strip()
        if t.startswith("```"):
            t = t.split("\n", 1)[1] if "\n" in t else ""
            t = t.rsplit("```", 1)[0]
        j = json.loads(t)
        items = j.get("lines") if isinstance(j, dict) else j
        out = []
        for it in items or []:
            if not isinstance(it, dict) or not str(it.get("text") or "").strip():
                continue
            s, e = float(it.get("start")), float(it.get("end"))
            if not (s == s and e == e) or s < 0:
                continue
            out.append({"start": round(s, 2), "end": round(max(s, e), 2), "text": str(it["text"]).strip()})
        return out or None
    except Exception:
        return None


def _out_limit(duration_sec):
    # about 11 tokens of answer per second of dialogue (measured: 4,593 for 489 s), with room to spare
    return int(min(65536, max(8192, 24 * float(duration_sec or 0) + 4096)))


def listen(job_id, vocals_path, work_dir, api_key, duration_sec, language_name="English", ask=None):
    """Asks once (one more try when the answer is empty or unreadable). The answer is saved in work_dir and reused.
    Returns {"lines": [...] or None, "usd", "model", "seconds", "reused"}. Never raises."""
    state = Path(work_dir) / STATE_FILE
    fp = ""
    try:
        st = Path(vocals_path).stat()
        fp = hashlib.sha256(f"{st.st_size}|{int(st.st_mtime)}|{language_name}".encode()).hexdigest()
        saved = json.loads(state.read_text(encoding="utf-8")) if state.exists() else None
        if saved and saved.get("v") == STATE_VERSION and saved.get("fp") == fp and saved.get("lines"):
            return dict(saved, reused=True)
    except Exception:
        pass
    out = {"lines": None, "usd": 0.0, "model": None, "seconds": 0.0, "reused": False}
    if not api_key:
        return out
    try:
        import speaker_vote_live
        audio = speaker_vote_live._audio_b64(vocals_path, work_dir)
    except Exception:
        audio = None
    if not audio:
        return out
    payload = {"contents": [{"parts": [{"inline_data": {"mime_type": "audio/mpeg", "data": audio}}, {"text": prompt_for(language_name)}]}],
               "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": _out_limit(duration_sec),
                                    "thinkingConfig": {"thinkingLevel": "medium"}}}
    if ask is None:
        def ask(p):
            import gemini_service
            import speaker_vote_live
            return gemini_service.call_gemini(api_key, p, timeout=900, models=speaker_vote_live.speaker_models())
    t0 = time.time()
    for _ in range(2):
        try:
            data, _err = ask(payload)
        except Exception:
            data = None
        if not isinstance(data, dict):
            continue
        try:
            import app_state
            app_state.record_gemini(job_id, data)
        except Exception:
            pass
        try:
            import resource_meter
            out["usd"] += float(resource_meter.gemini_usd(data) or 0.0)
        except Exception:
            pass
        out["model"] = data.get("modelVersion") or out["model"]
        try:
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"] if isinstance(p, dict) and not p.get("thought"))
        except Exception:
            text = ""
        lines = parse(text)
        if lines:
            out["lines"] = lines
            break
    out["seconds"] = round(time.time() - t0, 1)
    out["usd"] = round(out["usd"], 5)
    if out["lines"]:
        try:
            state.write_text(json.dumps(dict(out, v=STATE_VERSION, fp=fp)), encoding="utf-8")
        except Exception:
            pass
    return out


# ------------------------------------------------------------------ comparing the two transcripts

def _whisper_tokens(rows):
    """[(token, row index, word index, start, end)] in time order. A row without word times spreads its words over its time."""
    out = []
    for ri, r in enumerate(rows):
        words = r.get("words") or []
        if not words:
            ws = str(r.get("text") or "").split()
            s, e = float(r.get("start") or 0), float(r.get("end") or 0)
            step = (e - s) / max(1, len(ws))
            words = [{"word": w, "start": s + k * step, "end": s + (k + 1) * step} for k, w in enumerate(ws)]
        for wi, w in enumerate(words):
            for t in tokens(w.get("word")):
                out.append((t, ri, wi, float(w.get("start") or 0), float(w.get("end") or 0)))
    return out


def _gemini_tokens(glines):
    """[(token, line index, word index)] and the lines' words (original spelling)."""
    out, words = [], []
    for li, g in enumerate(glines):
        ws = str(g.get("text") or "").split()
        words.append(ws)
        for wi, w in enumerate(ws):
            for t in tokens(w):
                out.append((t, li, wi))
    return out, words


def _text_of(gtok, gwords):
    """The second listener's own words (spelling and punctuation kept) for a run of its tokens."""
    seen, parts = set(), []
    for _, li, wi in gtok:
        if (li, wi) not in seen:
            seen.add((li, wi))
            parts.append(gwords[li][wi])
    return " ".join(parts).strip()


def _similar(a_tokens, b_tokens):
    """How alike two stretches sound on paper (letters compared, so 'Lemke' and 'Lempke' count as nearly the same)."""
    return difflib.SequenceMatcher(None, " ".join(a_tokens), " ".join(b_tokens), autojunk=False).ratio()


_NUMBER_WORDS = set("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
                    "eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion and "
                    "dollar dollars percent point".split())


def _same_but_numbers(a, b):
    """'forty thousand dollars' and '40 000': the same words once numbers are left out."""
    def strip(ts):
        return [t for t in ts if not t.isdigit() and t not in _NUMBER_WORDS]
    has_num = any(t.isdigit() or t in _NUMBER_WORDS for t in a + b)
    return has_num and strip(a) == strip(b)


def _regions(opcodes, glue=2):
    """The differences between the two transcripts, with short runs of agreeing words (at most `glue`) between them joined in."""
    out = []
    for op, i1, i2, j1, j2 in opcodes:
        if op == "equal":
            if out and i2 - i1 <= glue and out[-1][4] == "open":
                out[-1][1], out[-1][3] = i2, j2      # tentatively joined; closed again below if nothing follows
                out[-1][5] += 1
            elif out:
                out[-1][4] = "closed"
            continue
        if out and out[-1][4] == "open" and out[-1][1] == i1 and out[-1][3] == j1:
            out[-1][1], out[-1][3] = i2, j2
            out[-1][5] = 0
        else:
            if out and out[-1][4] == "open" and out[-1][5]:
                pass
            out.append([i1, i2, j1, j2, "open", 0])
    return [(a, b, c, d) for a, b, c, d, _, _ in out]


def _trim(text):
    """A new line does not end in a lone 'and' / 'but' that belongs to the next sentence ("...fix the elevator. And")."""
    words = text.split()
    while len(words) > 1 and words[-1].strip(",.").lower() in ("and", "but", "so", "or") and words[-2][-1:] in ".?!":
        words.pop()
    return " ".join(words)


def _add_stretch(plan, gtoks, a, b, glines, gwords, clip_end, voiced=None):
    """New lines for what only the second listener heard, in the pause between Whisper's words a and b (either may be None).
    voiced(lo, hi) -> [(start, end)] where the separated voices really sound: then each sentence is placed on the voice nearest to
    where the second listener heard it, even a one-word one, and nothing is added where nothing sounds."""
    lo = a[4] if a is not None else 0.0
    hi = b[3] if b is not None else (float(clip_end) if clip_end else max(lo, float(glines[gtoks[-1][1]]["end"])) + 1.0)
    if hi - lo < ADD_MIN_GAP:
        return
    if voiced is not None:
        spans = None
        try:
            spans = [sp for sp in voiced(lo + 0.03, hi - 0.03) if sp[1] - sp[0] >= VOICE_MIN_SEC]
        except Exception:
            spans = None
        if spans is not None:
            _add_on_voice(plan, gtoks, a, glines, gwords, spans, lo, hi)
            return
    if len(gtoks) < ADD_MIN_TOKENS:
        return
    # one new line per sentence of the second listener (a pause can hold several people: real run, 28 Oct, 4:38-5:03)
    chunks = []
    for g in gtoks:
        if chunks and chunks[-1][-1][1] == g[1]:
            chunks[-1].append(g)
        else:
            chunks.append([g])
    merged = []
    for c in chunks:      # a one- or two-word sentence ("Huh?") goes with the sentence before it
        if merged and len(c) < ADD_MIN_TOKENS:
            merged[-1] = merged[-1] + c
        else:
            merged.append(c)
    if len(merged) > 1 and len(merged[0]) < ADD_MIN_TOKENS:
        merged[1] = merged[0] + merged[1]
        merged.pop(0)
    cursor = lo + 0.05
    for ci, c in enumerate(merged):
        gs, ge = float(glines[c[0][1]]["start"]), float(glines[c[-1][1]]["end"])
        nxt = float(glines[merged[ci + 1][0][1]]["start"]) if ci + 1 < len(merged) else hi
        need = max(0.6, SEC_PER_TOKEN * len(c))
        start = max(cursor, gs) if gs < hi - 0.3 else cursor
        limit = min(hi - 0.05, max(start + 0.3, nxt - 0.05))
        end = min(limit, max(start + need, ge))
        if end - start < 0.3:
            continue
        plan["add"].append({"start": round(start, 2), "end": round(end, 2), "text": _trim(_text_of(c, gwords)),
                            "after": a[1] if a is not None else -1})
        cursor = end + 0.05


def _add_on_voice(plan, gtoks, a, glines, gwords, spans, lo, hi):
    """One new line per sentence of the second listener, each on the stretches of voice nearest to where it heard that sentence
    (its own clock can be a second off: real run, 28 Oct, "Oh, my God." was heard 1 s early). A sentence that gets no voice is
    left out when it is short; nothing is added where no voice sounds at all."""
    if not spans:
        return
    chunks = []
    for g in gtoks:
        if chunks and chunks[-1][-1][1] == g[1]:
            chunks[-1].append(g)
        else:
            chunks.append([g])
    whole = lambda c: c[0][2] == 0 and c[-1][2] == len(gwords[c[-1][1]]) - 1     # the chunk is a whole sentence of the listener
    spans = sorted(spans)
    used = set()
    for c in chunks:
        if (len(c) < ADD_MIN_TOKENS and not whole(c)) or len(c) < ADD_MIN_TOKENS_HEARD:
            continue
        gs, ge = float(glines[c[0][1]]["start"]), float(glines[c[-1][1]]["end"])
        near = [k for k, sp in enumerate(spans) if k not in used and sp[1] >= gs - VOICE_WINDOW and sp[0] <= ge + VOICE_WINDOW]
        if not near:
            continue          # no voice where the second listener heard it: left out
        def closeness(k):
            sp = spans[k]
            ov = max(0.0, min(sp[1], ge) - max(sp[0], gs))
            return (-ov, abs((sp[0] + sp[1]) / 2.0 - (gs + ge) / 2.0))
        anchor = min(near, key=closeness)
        group = [anchor]
        for step in (-1, 1):           # the voice around it, while it goes on without a real pause
            k = anchor
            while (k + step) in near and abs(spans[k + step][0 if step > 0 else 1] - spans[k][1 if step > 0 else 0]) < VOICE_JOIN_GAP:
                k += step
                group.append(k)
        used.update(group)
        start = max(lo + 0.03, min(spans[k][0] for k in group) - 0.05)
        end = min(hi - 0.03, max(max(spans[k][1] for k in group) + 0.05, start + 0.3))
        if end - start < 0.25:
            continue
        plan["add"].append({"start": round(start, 2), "end": round(end, 2), "text": _trim(_text_of(c, gwords)),
                            "after": a[1] if a is not None else -1, "on_voice": True})


def compare(rows, glines, clip_end=None, voiced=None):
    """What to change: {"add": [{start, end, text, after}], "change": {row index: new text}}. Pure, never changes `rows`."""
    W = _whisper_tokens(rows)
    G, gwords = _gemini_tokens(glines or [])
    plan = {"add": [], "change": {}}
    if not W or not G:
        return plan
    ops = difflib.SequenceMatcher(None, [w[0] for w in W], [g[0] for g in G], autojunk=False).get_opcodes()
    swap = {}         # whisper token index -> the second listener's tokens that replace the run starting there ([] for the rest of the run)
    extra = {}        # row index -> {word index: tokens inserted before that word}  (words Whisper skipped inside a line)
    for i1, i2, j1, j2 in _regions(ops):
        wt, gt = [w[0] for w in W[i1:i2]], [g[0] for g in G[j1:j2]]
        if i1 == i2:      # only the second listener heard something here
            a = W[i1 - 1] if i1 > 0 else None
            b = W[i1] if i1 < len(W) else None
            if j2 - j1 < (ADD_MIN_TOKENS_HEARD if voiced is not None else ADD_MIN_TOKENS):
                continue
            if a is not None and b is not None and a[1] == b[1]:
                if j2 - j1 >= ADD_MIN_TOKENS:
                    extra.setdefault(a[1], {})[b[2]] = G[j1:j2]      # inside one line: the skipped words go in there
                continue
            _add_stretch(plan, G[j1:j2], a, b, glines, gwords, clip_end, voiced)
            continue
        if j1 == j2:
            continue      # only Whisper heard these words: Whisper stays
        # Whole sentences the second listener heard clearly before or after these words of Whisper belong in the pause there, not
        # in Whisper's line (real run, 28 Oct, job 80209392: everything heard between 4:39 and 5:03, the elevator sentence too,
        # went into the line "you got it mister..." because the two disagreed on its last words).
        core = list(G[j1:j2])
        w_start, w_end = W[i1][3], W[i2 - 1][4]
        lead = []
        while core and float(glines[core[0][1]]["end"]) < w_start - PEEL_MARGIN:
            lead.append(core.pop(0))
        tail = []
        while core and float(glines[core[-1][1]]["start"]) > w_end + PEEL_MARGIN:
            tail.insert(0, core.pop())
        if lead and i1 > 0 and W[i1 - 1][1] != W[i1][1]:
            _add_stretch(plan, lead, W[i1 - 1], W[i1], glines, gwords, clip_end, voiced)
        elif lead and i1 == 0:
            _add_stretch(plan, lead, None, W[i1], glines, gwords, clip_end, voiced)
        if tail and (i2 >= len(W) or W[i2 - 1][1] != W[i2][1]):
            _add_stretch(plan, tail, W[i2 - 1], W[i2] if i2 < len(W) else None, glines, gwords, clip_end, voiced)
        if not core:
            continue
        gt = [g[0] for g in core]
        if min(len(wt), len(gt)) < CHANGE_MIN_TOKENS or _similar(wt, gt) >= CHANGE_MAX_SIMILAR or _same_but_numbers(wt, gt):
            continue      # a small, spelling or number-writing difference: Whisper stays
        gs = min(float(glines[g[1]]["start"]) for g in core)
        ge = max(float(glines[g[1]]["end"]) for g in core)
        if gs > W[i2 - 1][4] + TIME_SLACK or ge < W[i1][3] - TIME_SLACK:
            # the two heard these words at different times: not the same speech (real run, 28 Oct: the closing lines at 7:56 were
            # 'rewritten' with words heard at 7:15). Whisper's words stay; the other words may still fill a pause near their own time.
            p = sum(1 for w in W if w[3] < gs)
            pa, pb = (W[p - 1] if p > 0 else None), (W[p] if p < len(W) else None)
            if (pa is None or pb is None or pa[1] != pb[1]) and (pa is None or pa[4] - 1.0 <= gs) and (pb is None or gs <= pb[3] + 1.0):
                _add_stretch(plan, core, pa, pb, glines, gwords, clip_end, voiced)
            continue
        # Whisper heard something clearly different here: the second listener's words replace it, shared out in order over the
        # lines Whisper's words belong to (its own times are too loose to place a sentence more exactly than that)
        G_sub = core
        by_row = []
        for k in range(i1, i2):
            if not by_row or by_row[-1][0] != W[k][1]:
                by_row.append([W[k][1], k, 0])
            by_row[-1][2] += 1
        n, done, m = i2 - i1, 0, len(G_sub)
        for ri, k0, cnt in by_row:
            if ri == by_row[-1][0]:
                take = m - done
            else:
                cut = done + int(round(m * cnt / n))
                ends = [k for k in range(max(done + 1, cut - 3), min(m, cut + 4)) if G_sub[k][1] != G_sub[k - 1][1]]
                if ends:      # cut where the second listener started a new sentence, when one is close
                    cut = min(ends, key=lambda k: abs(k - cut))
                take = max(0, cut - done)
            mine, theirs = [W[k][0] for k in range(k0, k0 + cnt)], [g[0] for g in G_sub[done:done + take]]
            part = G_sub[done:done + take]
            heard_near = bool(part) and (min(float(glines[g[1]]["start"]) for g in part) <= W[k0 + cnt - 1][4] + TIME_SLACK
                                         and max(float(glines[g[1]]["end"]) for g in part) >= W[k0][3] - TIME_SLACK)
            if theirs and heard_near and _similar(mine, theirs) < CHANGE_MAX_SIMILAR and not _same_but_numbers(mine, theirs):
                swap[k0] = G_sub[done:done + take]      # this line is clearly different (a line that only differs in spelling stays)
                for k in range(k0 + 1, k0 + cnt):
                    swap[k] = []
            done += take
    if not swap and not extra:
        return plan
    row_total, row_swapped = {}, {}
    for w in W:
        row_total[w[1]] = row_total.get(w[1], 0) + 1
    for k in swap:
        row_swapped[W[k][1]] = row_swapped.get(W[k][1], 0) + 1
    for ri in list(row_swapped):      # only a line that was heard differently as a whole is rewritten
        if row_total[ri] < CHANGE_ROW_MIN_TOKENS or row_swapped[ri] < CHANGE_ROW_SHARE * row_total[ri]:
            for k in [k for k in swap if W[k][1] == ri]:
                del swap[k]
    touched = {W[k][1] for k in swap} | set(extra)
    for ri in sorted(touched):
        words = rows[ri].get("words") or [{"word": w} for w in str(rows[ri].get("text") or "").split()]
        repl_word = {}
        for k, gt in swap.items():
            if W[k][1] == ri:
                repl_word.setdefault(W[k][2], []).append(gt)
        parts = []
        for wi, w in enumerate(words):
            if wi in (extra.get(ri) or {}):
                parts.append(_text_of(extra[ri][wi], gwords))
            if wi in repl_word:
                got = [g for lst in repl_word[wi] for g in lst]
                if got:
                    parts.append(_text_of(got, gwords))
                continue
            parts.append(str(w.get("word") or "").strip())
        text = " ".join(p for p in parts if p).strip()
        old, new = tokens(rows[ri].get("text")), tokens(text)
        if ri in row_swapped and ri not in extra and (len(new) < CHANGE_ROW_MIN_TOKENS or len(new) < 0.7 * len(old)):
            continue      # a rewrite that leaves a stub ("I'm gonna miss her." -> "How can I") is not trusted
        if text and new != old:
            plan["change"][ri] = text
    return plan


def apply(rows, plan, speaker_at=None):
    """Applies the plan. New lines get the speaker the detector heard there (speaker_at(t)) or the line before; the speaker check
    decides later. Returns (rows, added, changed) with the segment ids renumbered."""
    out = [dict(r) for r in rows]
    changed = []
    for ri, text in sorted(plan.get("change", {}).items()):
        r = out[ri]
        r["text_first"] = r.get("text")
        r["text"] = text
        ws = text.split()
        s, e = float(r.get("start") or 0), float(r.get("end") or 0)
        step = (e - s) / max(1, len(ws))
        r["words"] = [{"word": w, "start": round(s + k * step, 3), "end": round(s + (k + 1) * step, 3)} for k, w in enumerate(ws)]
        r["text_check"] = "changed"
        changed.append(r)
    added = []
    for a in plan.get("add", []):
        ref = out[a["after"]] if 0 <= a["after"] < len(out) else (out[0] if out else {})
        spk = None
        if speaker_at:
            try:
                spk = speaker_at((a["start"] + a["end"]) / 2.0)
            except Exception:
                spk = None
        ws = a["text"].split()
        step = (a["end"] - a["start"]) / max(1, len(ws))
        row = {"segment_id": "", "start": a["start"], "end": a["end"], "text": a["text"], "speaker": spk or ref.get("speaker") or "Speaker 1",
               "gender": ref.get("gender") or "male", "emotion": "neutral", "arabic_text": "",
               "words": [{"word": w, "start": round(a["start"] + k * step, 3), "end": round(a["start"] + (k + 1) * step, 3)} for k, w in enumerate(ws)],
               "text_check": "added", "second_listener": True}
        out.append(row)
        added.append(row)
    out.sort(key=lambda r: (float(r.get("start") or 0), float(r.get("end") or 0)))
    for i, r in enumerate(out):
        r["segment_id"] = f"seg_{i}"
    return out, added, changed


def _line_of_words(rows, glines):
    """{(row index, word index): the second listener's sentence (line index)} for the words both heard alike (exact word matches)."""
    W = _whisper_tokens(rows)
    G, _ = _gemini_tokens(glines or [])
    out = {}
    if not W or not G:
        return out, W, G
    sm = difflib.SequenceMatcher(None, [w[0] for w in W], [g[0] for g in G], autojunk=False)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            w, g = W[blk.a + k], G[blk.b + k]
            out.setdefault((w[1], w[2]), g[1])
    return out, W, G


STRAY_MAX_WORDS = 2       # a word or two at the edge of a line that belong to the sentence in the next/previous line move there
STRAY_MAX_GAP = 1.0


def cut_at_sentences(rows, glines):
    """One person per line: a line that holds words of two of the second listener's sentences (it writes a new one at every
    sentence and every change of speaker) is cut there, and a word or two at the edge of a line that belong to the next or
    previous line's sentence move over (real run, 28 Oct, 1:28-1:35: "don't move i | traced the call sir Give me the gun. are |
    you there very slowly" held the guard and the operator in each line). The words and their times are kept, nothing is lost;
    the new lines keep the line's speaker for the speaker check to decide. Returns (rows, number of cuts)."""
    if not rows or not glines:
        return rows, 0
    lw, W, G = _line_of_words(rows, glines)
    if not lw:
        return rows, 0
    pieces = []          # [row index, [word dicts], [labels]]
    for ri, r in enumerate(rows):
        words = r.get("words") or []
        if not words:
            pieces.append([ri, [], [], "keep"])          # a line without word times stays as it is
            continue
        if len(words) < 2 or r.get("manual_speaker") or r.get("locked"):
            pieces.append([ri, list(words), [lw.get((ri, wi)) for wi in range(len(words))], False])
            continue
        labels = [lw.get((ri, wi)) for wi in range(len(words))]
        known = [x for x in labels if x is not None]
        if not known:
            pieces.append([ri, list(words), labels, False])
            continue
        filled, last = [], None       # a word both did not hear alike belongs to the sentence of the word before it
        for x in labels:
            last = x if x is not None else last
            filled.append(last)
        first_known = next(x for x in labels if x is not None)
        filled = [x if x is not None else first_known for x in filled]
        cur = None
        for wi, w in enumerate(words):
            if cur is None or filled[wi] != cur[2][-1]:
                cur = [ri, [], [], True]
                pieces.append(cur)
            cur[1].append(w)
            cur[2].append(filled[wi])
    # a word or two at the edge of a line that belong to the sentence in the line next to it go there
    moved = 0
    for k in range(len(pieces)):
        p = pieces[k]
        if not p[1] or len(p[1]) > STRAY_MAX_WORDS or p[2][0] is None or rows[p[0]].get("manual_speaker") or rows[p[0]].get("locked"):
            continue
        prev = next((q for q in reversed(pieces[:k]) if q[1]), None)
        nxt = next((q for q in pieces[k + 1:] if q[1]), None)
        if prev is not None and (rows[prev[0]].get("manual_speaker") or rows[prev[0]].get("locked")):
            prev = None
        if nxt is not None and (rows[nxt[0]].get("manual_speaker") or rows[nxt[0]].get("locked")):
            nxt = None
        if prev is not None and prev[0] != p[0] and prev[2][-1] == p[2][0] \
                and float(p[1][0]["start"]) - float(prev[1][-1]["end"]) <= STRAY_MAX_GAP:
            prev[1].extend(p[1]); prev[2].extend(p[2]); p[1], p[2] = [], []
            moved += 1
        elif nxt is not None and nxt[0] != p[0] and nxt[2][0] == p[2][-1] \
                and float(nxt[1][0]["start"]) - float(p[1][-1]["end"]) <= STRAY_MAX_GAP:
            nxt[1][:0] = p[1]; nxt[2][:0] = p[2]; p[1], p[2] = [], []
            moved += 1
    out = []
    for ri, words, labels, kind in pieces:
        if kind == "keep":
            out.append(dict(rows[ri]))
            continue
        if not words:
            continue
        nr = dict(rows[ri])
        nr["words"] = words
        nr["text"] = " ".join(str(w.get("word") or "").strip() for w in words).strip()
        nr["start"] = round(float(words[0]["start"]), 2)
        nr["end"] = round(max(float(words[-1]["end"]), float(words[0]["start"]) + 0.2), 2)
        out.append(nr)
    cuts = max(0, len(out) - sum(1 for r in rows if r.get("words"))) + moved
    # a line whose words did not change keeps its own text (spelling, punctuation)
    for nr in out:
        src = next((r for r in rows if r.get("segment_id") == nr.get("segment_id")), None)
        if src is not None and len(src.get("words") or []) == len(nr["words"]) and all(
                a is b for a, b in zip(src.get("words") or [], nr["words"])):
            nr["text"], nr["start"], nr["end"] = src.get("text"), src.get("start"), src.get("end")
    out.sort(key=lambda r: (float(r.get("start") or 0), float(r.get("end") or 0)))
    for i, r in enumerate(out):
        r["segment_id"] = f"seg_{i}"
    return out, cuts


def mark_unheard(rows, glines, near=UNHEARD_NEAR):
    """Marks lines the second listener did not hear at all ("check text", maybe not speech): none of their words were heard alike and
    nothing was heard within `near` seconds. Returns the marked lines. Only marks; the person decides."""
    if not rows or not glines:
        return []
    lw, W, G = _line_of_words(rows, glines)
    heard_rows = {ri for (ri, wi) in lw}
    spans = [(float(g.get("start") or 0), float(g.get("end") or 0)) for g in glines]
    marked = []
    for ri, r in enumerate(rows):
        if ri in heard_rows or r.get("text_check") or r.get("manual_speaker"):
            continue
        s, e = float(r.get("start") or 0), float(r.get("end") or 0)
        if any(gs - near <= e and ge + near >= s for gs, ge in spans):
            continue
        r["text_check"] = "unheard"
        marked.append(r)
    return marked


def voiced_spans(path, lo, hi):
    """[(start, end)] where the separated voices sound between lo and hi seconds (a level clearly above the quiet around it).
    Reads only that part of the file. [] when nothing sounds; raises when the file cannot be read."""
    import numpy as np
    import soundfile as sf
    info = sf.info(str(path))
    sr = info.samplerate
    a0, a1 = max(0.0, lo - 4.0), min(float(info.duration), hi + 4.0)      # the quiet is judged on a wider stretch
    if a1 - a0 < 0.1 or hi <= lo:
        return []
    x, _ = sf.read(str(path), start=int(a0 * sr), frames=int((a1 - a0) * sr), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    win = max(1, int(0.03 * sr))
    n = len(x) // win
    if n < 3:
        return []
    db = 20 * np.log10(np.sqrt((x[:n * win].reshape(n, win) ** 2).mean(axis=1)) + 1e-9)
    floor = float(np.percentile(db, 10))
    loud = db > max(floor + 12.0, -50.0)
    out, start = [], None
    for i, flag in enumerate(loud):
        t = a0 + i * 0.03
        if flag and start is None:
            start = t
        elif not flag and start is not None:
            out.append([start, t])
            start = None
    if start is not None:
        out.append([start, a0 + n * 0.03])
    merged = []
    for sp in out:          # two stretches less than 0.25 s apart are one
        if merged and sp[0] - merged[-1][1] < 0.25:
            merged[-1][1] = sp[1]
        else:
            merged.append(sp)
    return [(round(max(lo, s), 3), round(min(hi, e), 3)) for s, e in merged if e > lo and s < hi and min(hi, e) - max(lo, s) > 0]


def clock(t):
    t = max(0.0, float(t))
    return f"{int(t // 60)}:{t % 60:04.1f}"


def summary(heard, added, changed):
    head = (f"{len(heard.get('lines') or [])} sentences heard by {heard.get('model') or 'the second listener'}"
            + (" (saved answer reused)" if heard.get("reused") else f" in {heard.get('seconds', 0)} s, cost ${float(heard.get('usd') or 0):.4f}"))
    parts = [head, f"{len(added)} missed sentence(s) added", f"{len(changed)} line(s) rewritten where the speech recognition heard something else"]
    if added:
        parts.append("added: " + "; ".join(f"{clock(r['start'])} \"{r['text'][:60]}\"" for r in added[:10]))
    if changed:
        parts.append("rewritten: " + "; ".join(f"{clock(r['start'])} \"{str(r.get('text_first'))[:40]}\" -> \"{r['text'][:40]}\"" for r in changed[:10]))
    return "; ".join(parts)
