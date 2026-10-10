"""AI line planner (TRIAL, switched off by default): asks Gemini where the lines of the transcript should be cut.

The speech recogniser and the subtitle give the words; they do not know who says which words when two people share one
subtitle cue or one breath. A language model can read the dialogue and see "this sentence belongs to the next voice".
It is only allowed to do two things, and both are checked here by plain rules before they are applied:

* {"op": "move_words", "from": id, "to": id, "n": 1..6, "side": "end"|"start"}  - the last (or first) n words of one line
  move to the line right after (or right before) it;
* {"op": "merge", "a": id, "b": id}  - two neighbouring lines of the SAME speaker become one line.

It never writes or changes a word: the words before and after must be identical or the whole window is thrown away.
Any failure (no key, no answer, a bad answer, too slow) leaves the lines exactly as they were.

Switch on with the environment variable LD_LINE_PLANNER=1 (needs GEMINI_API_KEY). Measure first with line_planner_trial.py.
plan_lines(job_id, rows, api_key, vocals=None) -> (rows, report)
"""
import json
import os
import re
import time

import line_tidy

ENABLED = os.environ.get("LD_LINE_PLANNER", "0").strip().lower() in ("1", "true", "yes", "on")
WINDOW = 24                 # lines judged per request
CONTEXT = 3                 # lines before and after a window that are shown for reading only
MAX_WORDS_MOVED = 6         # a move bigger than this is not a "stray beginning"
MAX_OPS_PER_WINDOW = 8
MERGE_MAX_GAP = 1.0         # seconds between two lines of one speaker that may still be one line
MAX_LINE_SEC = line_tidy.MAX_LINE_SEC
MAX_WINDOWS = 40
BUDGET_SEC = 180.0          # the whole planner never takes longer than this; what is not done stays as it was

PROMPT = """You are checking where the lines of a dialogue transcript are cut, for dubbing.
Each line below has an id, a speaker, its time and its text. The cuts came from an automatic recogniser and are sometimes
wrong right at the edge between two speakers: the first words of a sentence stay at the end of the PREVIOUS speaker's line,
or the last words of a sentence end up at the start of the NEXT line. One sentence is spoken by one person.
Subtitle-style text is only a transcript: one subtitle cue can hold two speakers.

Use meaning and context (who asks, who answers, who interrupts, who stutters or repeats). Only propose a change when you
are sure. If a line is fine, leave it. Interruptions that end with a dash are real and stay as they are.

You may only use these operations (lines are listed in time order; both lines must be neighbours):
- {"op":"move_words","from":"<id>","to":"<id>","n":<1-6>,"side":"end"}   the LAST n words of line "from" move to the START of the next line "to"
- {"op":"move_words","from":"<id>","to":"<id>","n":<1-6>,"side":"start"} the FIRST n words of line "from" move to the END of the previous line "to"
- {"op":"merge","a":"<id>","b":"<id>"}   two neighbouring lines of the SAME speaker are really one line
Never reword, add or delete words. Lines marked (context) may be read but never changed.
Return ONLY JSON: {"ops":[ ... ]}   (an empty list when nothing needs to change)

Lines:
"""


def _norm_words(rows):
    return [w for r in rows for w in re.findall(r"\S+", str(r.get("text") or ""))]


def _label(row):
    return str(row.get("speaker") or row.get("speaker_id") or "?")


def build_prompt(window, before, after):
    lines = []
    for tag, group in (("(context)", before), ("", window), ("(context)", after)):
        for r in group:
            lines.append(f'{r["segment_id"]} | {_label(r)} | {float(r["start"]):.2f}-{float(r["end"]):.2f} | '
                         f'{str(r.get("text") or "").strip()} {tag}'.rstrip())
    return PROMPT + "\n".join(lines)


def ask_gemini(job_id, prompt, api_key):
    """Returns (parsed JSON or None, {"in": n, "out": n, "thoughts": n}). Never raises."""
    usage = {"in": 0, "out": 0, "thoughts": 0}
    try:
        import gemini_service
        payload = {"contents": [{"parts": [{"text": prompt}]}],
                   "generationConfig": {"maxOutputTokens": 4096, "responseMimeType": "application/json"}}
        data, err = gemini_service.call_gemini(api_key, payload, timeout=60)
        try:
            gemini_service.record_gemini(job_id, data)
        except Exception:
            pass
        if data is None:
            print(f"[line_planner] no answer: {err}")
            return None, usage
        u = data.get("usageMetadata") or {}
        usage = {"in": int(u.get("promptTokenCount", 0) or 0), "out": int(u.get("candidatesTokenCount", 0) or 0),
                 "thoughts": int(u.get("thoughtsTokenCount", 0) or 0)}
        text = gemini_service._strip_code_fences(data["candidates"][0]["content"]["parts"][0]["text"])
        return json.loads(text), usage
    except Exception as ex:
        print(f"[line_planner] unusable answer: {ex}")
        return None, usage


def _ops_from(answer):
    if isinstance(answer, dict):
        answer = answer.get("ops")
    return [o for o in answer if isinstance(o, dict)] if isinstance(answer, list) else []


def _index(rows):
    return {r["segment_id"]: i for i, r in enumerate(rows)}


def _check(op, rows, allowed):
    """None when the operation is allowed, else the reason it is refused. `allowed` = ids that may change."""
    idx = _index(rows)
    kind = op.get("op")
    if kind == "merge":
        a, b = op.get("a"), op.get("b")
        if a not in allowed or b not in allowed or a not in idx or b not in idx:
            return "line not in the window"
        i, j = idx[a], idx[b]
        if j != i + 1:
            return "not neighbours"
        ra, rb = rows[i], rows[j]
        if ra.get("locked") or rb.get("locked"):
            return "locked"
        if not line_tidy._same_voice(ra, rb):
            return "different speakers"
        if float(rb["start"]) - float(ra["end"]) > MERGE_MAX_GAP:
            return "too far apart"
        if float(rb["end"]) - float(ra["start"]) > MAX_LINE_SEC:
            return "too long"
        return None
    if kind == "move_words":
        src, dst, side = op.get("from"), op.get("to"), op.get("side")
        try:
            n = int(op.get("n"))
        except Exception:
            return "n is not a number"
        if src not in allowed or dst not in allowed or src not in idx or dst not in idx:
            return "line not in the window"
        if side not in ("end", "start") or not 1 <= n <= MAX_WORDS_MOVED:
            return "bad side or size"
        i, j = idx[src], idx[dst]
        if j != (i + 1 if side == "end" else i - 1):
            return "not the neighbour on that side"
        rs, rd = rows[i], rows[j]
        if rs.get("locked") or rd.get("locked"):
            return "locked"
        if line_tidy._same_voice(rs, rd):
            return "same speaker (merge instead)"
        if len(line_tidy._tokens(rs.get("text"))) - n < 1:
            return "would empty the line"
        return None
    return "unknown operation"


def _apply(op, rows, vocals):
    idx = _index(rows)
    if op["op"] == "merge":
        i = idx[op["a"]]
        a, b = rows[i], rows[i + 1]
        a["end"] = b["end"]
        a["text"] = line_tidy._join_text(a.get("text"), b.get("text"))
        if "words" in a or "words" in b:
            a["words"] = list(a.get("words") or []) + list(b.get("words") or [])
        del rows[i + 1]
        return
    n = int(op["n"])
    if op["side"] == "end":
        a, b = rows[idx[op["from"]]], rows[idx[op["to"]]]
        line_tidy.shift_tail(a, b, len(line_tidy._tokens(a.get("text"))) - n, vocals)
    else:
        a, b = rows[idx[op["to"]]], rows[idx[op["from"]]]
        line_tidy.shift_head(a, b, n)


def plan_lines(job_id, rows, api_key, vocals=None, ask=None, budget_sec=None, window=None):
    """Returns (rows, report). `ask(prompt) -> (parsed, usage)` can be injected (tests, the trial); the default asks Gemini."""
    window = window or WINDOW
    report = {"windows": 0, "asked": 0, "applied": 0, "refused": 0, "reverted_windows": 0, "tokens_in": 0, "tokens_out": 0,
              "tokens_thoughts": 0, "seconds": 0.0, "refusals": []}
    started = time.monotonic()
    budget = BUDGET_SEC if budget_sec is None else budget_sec
    if ask is None:
        if not api_key:
            report["skipped"] = "no key"
            return rows, report
        ask = lambda prompt: ask_gemini(job_id, prompt, api_key)
    try:
        work = [dict(r) for r in rows]
        for r in work:
            if isinstance(r.get("words"), list):
                r["words"] = [dict(w) if isinstance(w, dict) else w for w in r["words"]]
        pos = 0
        while pos < len(work) and report["windows"] < MAX_WINDOWS:
            if time.monotonic() - started > budget:
                report["stopped"] = "time budget"
                break
            core = work[pos:pos + window]
            before, after = work[max(0, pos - CONTEXT):pos], work[pos + window:pos + window + CONTEXT]
            report["windows"] += 1
            answer, usage = ask(build_prompt(core, before, after))
            report["tokens_in"] += usage.get("in", 0)
            report["tokens_out"] += usage.get("out", 0)
            report["tokens_thoughts"] += usage.get("thoughts", 0)
            ops = _ops_from(answer)[:MAX_OPS_PER_WINDOW]
            report["asked"] += len(ops)
            allowed = {r["segment_id"] for r in core}
            snapshot = [dict(r) for r in work]
            words_before = _norm_words(work)
            applied = 0
            for op in ops:
                why = _check(op, work, allowed)
                if why:
                    report["refused"] += 1
                    report["refusals"].append(why)
                    continue
                _apply(op, work, vocals)
                applied += 1
            if applied and _norm_words(work) != words_before:     # the words must come out exactly as they went in
                work = snapshot
                report["reverted_windows"] += 1
                applied = 0
            report["applied"] += applied
            pos += window - (len(snapshot) - len(work))          # merges shorten the list: the next window starts after this one
        report["seconds"] = round(time.monotonic() - started, 2)
        if not report["applied"]:
            return rows, report
        for i, r in enumerate(work):
            r["segment_id"] = f"seg_{i}"
        return work, report
    except Exception as ex:
        print(f"[line_planner] skipped: {ex}")
        report["error"] = str(ex)
        report["seconds"] = round(time.monotonic() - started, 2)
        return rows, report
