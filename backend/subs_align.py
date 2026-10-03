"""Subtitle file -> corrects the English transcript.

The speech recogniser sometimes hears a word wrong (names above all). When the
user has the English subtitle of the same video, the subtitle is the better
spelling of the same words. This module lines the subtitle up with the
transcript by the WORDS (not by the clock) and puts the subtitle's wording into
the transcript's lines.

Why by words: a 3-minute clip cut from a film comes with the subtitle of the
whole film, with the film's own clock (the clip starts at minute 72, say), and
sometimes at a slightly different speed. Matching words does not care about
either. Everything of the subtitle outside the clip is ignored automatically.

Used by the short dub page (POST /api/subs/align) and by the long dub analysis
(before the translation, so the Arabic is made from the corrected English).
Pure Python, no network, costs nothing.

    new_rows, report = correct_rows(rows, subtitle_text, filename, mode="auto", add_missed=False)

rows: [{"segment_id", "start", "end", "text", ...}] (other keys are kept).
mode: "auto"  - a new line only where the subtitle starts a dash line (a new speaker); wrapped
                lines and cut sentences follow the transcript's own lines
      "join"  - never splits a transcript line; a subtitle's lines are written together
      "lines" - every subtitle line is its own line (one line per speaker/line)
"""
import html
import re
import uuid
from bisect import bisect_left
from difflib import SequenceMatcher

MODES = ("auto", "join", "lines")
MAX_CHARS = 4 * 1024 * 1024        # biggest subtitle text accepted
MIN_ANCHOR = 3                     # shortest run of identical words that counts as a certain match
MIN_ROW_TRUST = 0.30               # share of a line's words found in the subtitle before the line is rewritten
MIN_SPLIT_SEC = 0.4                # a split-off line is never shorter than this
FUZZY = 0.78                       # similarity at which two spellings count as the same word
MAX_CELLS = 360_000                # biggest piece the fine alignment will work on (words x words)

_DASHES = "-‐‑‒–—―"
_TIME = r"(?:(\d+):)?(\d{1,2}):(\d{1,2})[.,](\d{1,3})"
_TIME_RE = re.compile(_TIME)
_SENTENCE_END = re.compile(r"[.!?…؟][\"'”’)\]]*$")


REASONS = {
    "empty": "The subtitle file is empty.",
    "too_big": "This subtitle file is too big. It should be a normal subtitle file of one film or video.",
    "no_cues": "This doesn't look like a subtitle file. Please choose an .srt, .vtt, .sbv or .ass file.",
    "arabic": "This subtitle is in Arabic. Please choose the English subtitle of the same video.",
    "no_match": "We couldn't find this video's speech in the subtitle file, so nothing was changed. "
                "Please check that it is the English subtitle of the same video.",
}


def reason_message(reason):
    return REASONS.get(reason, "We couldn't use this subtitle file.")


def check_file(text, filename=""):
    """(True, number_of_subtitles) or (False, reason). Only looks at the file itself."""
    if not text or not str(text).strip():
        return False, "empty"
    if len(text) > MAX_CHARS:
        return False, "too_big"
    cues = parse_subtitles(text, filename)
    if not cues:
        return False, "no_cues"
    if sum(1 for c in cues if _is_arabic(" ".join(t for t, _ in c["lines"]))) > len(cues) / 2:
        return False, "arabic"
    return True, len(cues)


# ------------------------------------------------------------------ reading the file

def _secs(m):
    h, mi, s, frac = m
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + float("0." + frac)


def _clean_line(line):
    """One subtitle line -> (text, dash). Tags, sound descriptions and music marks are removed."""
    s = html.unescape(line)
    s = re.sub(r"<[^>]*>", "", s)
    s = re.sub(r"\{[^}]*\}", "", s)
    s = s.replace("‎", "").replace("‏", "").replace("‪", "").replace("‬", "")
    if "♪" in s or "♫" in s or "♩" in s:      # music notes: a song, not speech
        return "", False
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = s.strip()
    dash = False
    m = re.match(r"^[" + _DASHES + r"]+\s*", s)
    if m:
        dash = True
        s = s[m.end():]
    s = re.sub(r"^[A-Z][A-Z0-9 .'\-]{1,24}:\s+", "", s)      # "JOHN: Hello" -> "Hello"
    s = re.sub(r"\s+", " ", s).strip()
    if not re.search(r"\w", s):
        return "", dash
    return s, dash


def _cue(start, end, raw_lines):
    lines = []
    for ln in raw_lines:
        for part in re.split(r"\\[Nn]", ln):          # the line break of .ass files
            t, dash = _clean_line(part)
            if t:
                lines.append((t, dash))
    if not lines or end < start:
        return None
    return {"start": start, "end": end, "lines": lines}


def parse_subtitles(text, filename=""):
    """SRT, VTT, SBV and ASS/SSA -> [{"start", "end", "lines": [(text, starts_with_dash)]}], sorted by time."""
    text = (text or "").lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    if re.search(r"^Dialogue:", text, re.M):                                   # ASS / SSA
        for ln in text.split("\n"):
            if not ln.startswith("Dialogue:"):
                continue
            parts = ln[len("Dialogue:"):].split(",", 9)
            if len(parts) < 10:
                continue
            a, b = _TIME_RE.match(parts[1].strip()), _TIME_RE.match(parts[2].strip())
            if not a or not b:
                continue
            c = _cue(_secs(a.groups()), _secs(b.groups()), [parts[9]])
            if c:
                cues.append(c)
    else:
        for block in re.split(r"\n\s*\n", text):
            lines = [l for l in block.split("\n") if l.strip() != ""]
            if not lines:
                continue
            ti = next((k for k, l in enumerate(lines) if "-->" in l), -1)
            if ti >= 0:                                                        # SRT / VTT
                left, right = lines[ti].split("-->", 1)
                a, b = _TIME_RE.search(left), _TIME_RE.search(right)
                if not a or not b:
                    continue
                c = _cue(_secs(a.groups()), _secs(b.groups()), lines[ti + 1:])
            else:                                                              # SBV: "0:00:01.000,0:00:03.000"
                m = re.match(r"^\s*(\d+:\d{2}:\d{2}\.\d+)\s*,\s*(\d+:\d{2}:\d{2}\.\d+)\s*$", lines[0])
                if not m:
                    continue
                a, b = _TIME_RE.match(m.group(1)), _TIME_RE.match(m.group(2))
                c = _cue(_secs(a.groups()), _secs(b.groups()), lines[1:])
            if c:
                cues.append(c)
    cues.sort(key=lambda c: (c["start"], c["end"]))
    return cues


# ------------------------------------------------------------------ words

def norm(tok):
    t = str(tok).lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"[^\w']", "", t, flags=re.U)
    return t.replace("'", "").replace("_", "")


def _surface_tokens(text):
    text = re.sub(r"(?<=\S)([—–])(?=\S)", r"\1 ", text)
    out = []
    for t in text.split():
        if norm(t):
            out.append(t)
        elif out:
            out[-1] += t                  # a lone mark ("...", "?!") stays with the word before it
    return out


def _is_arabic(text):
    letters = re.findall(r"[A-Za-z؀-ۿ]", text)
    if not letters:
        return False
    return sum(1 for c in letters if "؀" <= c <= "ۿ") / len(letters) > 0.5


def build_stream(cues, mode):
    """The subtitle as one stream of words. Every word knows its cue, its 'line' (for splitting) and its time."""
    norms, surfs, cue_ix, unit, times = [], [], [], [], []
    u = 0
    for ci, c in enumerate(cues):
        lines = c["lines"]
        total_chars = sum(len(t) for t, _ in lines) or 1
        done = 0
        prev_ended = True
        for li, (t, dash) in enumerate(lines):
            if mode == "lines":
                u += 1
            elif mode == "auto":
                if dash and (li > 0 or norms):
                    u += 1
            toks = _surface_tokens(t)
            n_chars = len(t)
            for k, tk in enumerate(toks):
                nk = norm(tk)
                if not nk:
                    continue
                frac = (done + n_chars * (k + 0.5) / len(toks)) / total_chars
                norms.append(nk)
                surfs.append(tk)
                cue_ix.append(ci)
                unit.append(u)
                times.append(c["start"] + (c["end"] - c["start"]) * frac)
            done += n_chars
    return {"norm": norms, "surf": surfs, "cue": cue_ix, "unit": unit, "t": times}


# ------------------------------------------------------------------ alignment

_sim_cache = {}


def _similar(a, b):
    if a == b:
        return 2
    if len(a) < 4 or len(b) < 4 or abs(len(a) - len(b)) > 3:
        return 0
    key = (a, b) if a < b else (b, a)
    v = _sim_cache.get(key)
    if v is None:
        if len(_sim_cache) > 200000:
            _sim_cache.clear()
        v = 1 if SequenceMatcher(None, a, b).ratio() >= FUZZY else 0
        _sim_cache[key] = v
    return v


def _nw(A, B, free_b_start=False, free_b_end=False):
    """Word-by-word alignment of two short lists. Returns [(i, j)] for words paired up
    (identical, alike, or simply standing in the same place = a wrong word)."""
    m, n = len(A), len(B)
    if m == 0 or n == 0 or m * n > MAX_CELLS:
        return []
    GAP, MISS = -1.5, -1.0
    sc = [[0.0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        sc[i][0] = sc[i - 1][0] + GAP
    if not free_b_start:
        for j in range(1, n + 1):
            sc[0][j] = sc[0][j - 1] + GAP
    for i in range(1, m + 1):
        ai = A[i - 1]
        row, prow = sc[i], sc[i - 1]
        for j in range(1, n + 1):
            s = _similar(ai, B[j - 1])
            d = prow[j - 1] + (3.0 if s == 2 else (1.5 if s == 1 else MISS))
            u = prow[j] + GAP
            l = row[j - 1] + GAP
            row[j] = d if d >= u and d >= l else (u if u >= l else l)
    j = n
    if free_b_end:
        j = max(range(n + 1), key=lambda k: sc[m][k])
    i = m
    out = []
    while i > 0 and j > 0:
        s = _similar(A[i - 1], B[j - 1])
        d = sc[i - 1][j - 1] + (3.0 if s == 2 else (1.5 if s == 1 else MISS))
        if abs(sc[i][j] - d) < 1e-9:
            out.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif abs(sc[i][j] - (sc[i - 1][j] + GAP)) < 1e-9:
            i -= 1
        else:
            j -= 1
    out.reverse()
    return out


def _anchor_clusters(A, B):
    """Runs of identical words (3+) shared by the transcript and the subtitle, grouped into
    stretches where the two keep pace. Returns [[(i, j, n), ...], ...]."""
    sm = SequenceMatcher(None, A, B, autojunk=False)
    strong = [(b.a, b.b, b.size) for b in sm.get_matching_blocks() if b.size >= MIN_ANCHOR]
    clusters, cur = [], []
    for blk in strong:
        if cur:
            pi, pj, pn = cur[-1]
            gap_a = blk[0] - (pi + pn)
            gap_b = blk[1] - (pj + pn)
            if gap_b > 3 * gap_a + 40:
                clusters.append(cur)
                cur = []
        cur.append(blk)
    if cur:
        clusters.append(cur)
    totals = [sum(n for _, _, n in c) for c in clusters]
    if not totals:
        return [], 0
    best = max(totals)
    keep = [c for c, t in zip(clusters, totals) if t >= max(6, 0.1 * best) or t >= 0.5 * len(A)]
    if not keep:
        keep = [clusters[totals.index(best)]]
    return keep, best


def _align_all(A, B):
    """[(i, j)] for every transcript word that has a place in the subtitle (monotonic)."""
    clusters, best = _anchor_clusters(A, B)
    pairs = []
    lead_from_a, lead_from_b = 0, 0              # where the previous cluster's tail stopped (A side, B side)
    for ci, cl in enumerate(clusters):
        i0, j0 = cl[0][0], cl[0][1]
        # words before the first certain run: the film may go on to the left, so B's left end is free
        lead_b = min(j0 - lead_from_b, int((i0 - lead_from_a) * 1.6) + 12)
        for (ii, jj) in _nw(A[lead_from_a:i0], B[j0 - lead_b:j0], free_b_start=True):
            pairs.append((lead_from_a + ii, j0 - lead_b + jj))
        for k, (bi, bj, bn) in enumerate(cl):
            for t in range(bn):
                pairs.append((bi + t, bj + t))
            if k + 1 < len(cl):
                ni, nj = cl[k + 1][0], cl[k + 1][1]
                for (ii, jj) in _nw(A[bi + bn:ni], B[bj + bn:nj]):
                    pairs.append((bi + bn + ii, bj + bn + jj))
        li, lj = cl[-1][0] + cl[-1][2], cl[-1][1] + cl[-1][2]
        if ci + 1 < len(clusters):
            gap_a = clusters[ci + 1][0][0] - li
            end_a = li + gap_a // 2                  # the words between two stretches are shared half and half
            room_b = clusters[ci + 1][0][1] - lj
        else:
            end_a = len(A)
            room_b = len(B) - lj
        tail_b = min(room_b, int((end_a - li) * 1.6) + 12)
        for (ii, jj) in _nw(A[li:end_a], B[lj:lj + tail_b], free_b_end=True):
            pairs.append((li + ii, lj + jj))
        lead_from_a, lead_from_b = end_a, lj + tail_b
    pairs = sorted(set(pairs))
    clean, last_i, last_j = [], -1, -1           # strictly monotonic: one stretch never crosses another
    for i, j in pairs:
        if i > last_i and j > last_j:
            clean.append((i, j))
            last_i, last_j = i, j
    return clean, best


# ------------------------------------------------------------------ the correction

def _row_tokens(row):
    toks = []
    for t in str(row.get("text") or "").split():
        n = norm(t)
        if n:
            toks.append(n)
    return toks


def correct_rows(rows, subtitle_text, filename="", mode="auto", add_missed=False, id_factory=None):
    """Returns (new_rows, report). Never raises for bad input: report["ok"] is False with a "reason"."""
    mode = mode if mode in MODES else "auto"
    report = {"ok": False, "mode": mode, "rows": len(rows or []), "reason": "", "cues": 0, "changed": 0, "style": 0,
              "split": 0, "added": 0, "unmatched": 0, "ignored_cues": 0, "missed": 0, "locked_skipped": 0,
              "arabic_stale": 0}
    rows = [dict(r) for r in (rows or [])]
    if not subtitle_text or not str(subtitle_text).strip():
        report["reason"] = "empty"
        return rows, report
    if len(subtitle_text) > MAX_CHARS:
        report["reason"] = "too_big"
        return rows, report
    cues = parse_subtitles(subtitle_text, filename)
    report["cues"] = len(cues)
    if not cues:
        report["reason"] = "no_cues"
        return rows, report
    if sum(1 for c in cues if _is_arabic(" ".join(t for t, _ in c["lines"]))) > len(cues) / 2:
        report["reason"] = "arabic"
        return rows, report
    stream = build_stream(cues, mode)
    B = stream["norm"]
    # transcript words, remembering which row each belongs to
    A, a_row = [], []
    for ri, r in enumerate(rows):
        for n in _row_tokens(r):
            A.append(n)
            a_row.append(ri)
    if len(A) > 80000 or len(B) > 250000:             # far beyond any real video / subtitle: refuse instead of grinding
        report["reason"] = "too_big"
        return rows, report
    if len(A) < 3 or len(B) < 3:
        report["reason"] = "no_match"
        return rows, report
    pairs, best_run = _align_all(A, B)
    # a real match has one stretch of the subtitle that carries a good share of the transcript's words;
    # another film (or another language's subtitle) gives only scattered short matches
    if not pairs or best_run < max(5, 0.12 * len(A)):
        report["reason"] = "no_match"
        return rows, report
    report["ok"] = True

    # which transcript word each subtitle word was paired with
    b_owner = {}
    for i, j in pairs:
        b_owner[j] = a_row[i]
    first_j, last_j = pairs[0][1], pairs[-1][1]

    # quality of every row: how many of its words are really found in the subtitle
    good = [0] * len(rows)
    ntok = [0] * len(rows)
    for ri in a_row:
        ntok[ri] += 1
    for i, j in pairs:
        if _similar(A[i], B[j]):
            good[a_row[i]] += 1
    share = [(good[k] / ntok[k]) if ntok[k] else 0.0 for k in range(len(rows))]
    trusted = [False] * len(rows)
    for k in range(len(rows)):
        if ntok[k] == 0:
            continue
        if share[k] >= MIN_ROW_TRUST:
            trusted[k] = True
        elif 0 < k < len(rows) - 1 and share[k - 1] >= 0.5 and share[k + 1] >= 0.5:
            trusted[k] = True                      # wrong all through, but between two certain lines: still that spot

    # give every subtitle word inside the matched stretch to a row
    cue, unit = stream["cue"], stream["unit"]
    owner, orphans = {}, []                        # orphans: subtitle words inside the video that no transcript word answers to
    prev_j = None
    pending = []
    for j in range(first_j, last_j + 1):
        if j not in b_owner:
            pending.append(j)
            continue
        r_next = b_owner[j]
        if pending:
            r_prev = b_owner[prev_j]
            for pos, pj in enumerate(pending):
                if r_prev == r_next:
                    owner[pj] = r_prev
                    continue
                with_prev = cue[pj] == cue[prev_j]
                with_next = cue[pj] == cue[j]
                if with_prev and with_next:
                    owner[pj] = r_prev if pos < len(pending) / 2.0 else r_next
                elif with_prev:
                    owner[pj] = r_prev
                elif with_next:
                    owner[pj] = r_next
                else:
                    orphans.append(pj)
            pending = []
        owner[j] = r_next
        prev_j = j
    row_words = {}
    for j in sorted(owner):
        row_words.setdefault(owner[j], []).append(j)

    def surf_join(js):
        return " ".join(stream["surf"][j] for j in js)

    def make_id():
        return id_factory() if id_factory else "sub_" + uuid.uuid4().hex[:8]

    new_rows = []
    n_changed = n_style = n_split = 0
    for k, r in enumerate(rows):
        js = row_words.get(k, [])
        if r.get("locked"):
            if js:
                report["locked_skipped"] += 1
            new_rows.append(r)
            continue
        if not trusted[k] or not js:
            if ntok[k]:
                report["unmatched"] += 1
            new_rows.append(r)
            continue
        old_text = str(r.get("text") or "")
        old_norm = [norm(t) for t in old_text.split() if norm(t)]
        groups, cur_u = [], None                    # the words, grouped by subtitle line (join mode: one group)
        for j in js:
            if mode == "join" or unit[j] != cur_u:
                if mode != "join" or not groups:
                    groups.append([])
                cur_u = unit[j]
            groups[-1].append(j)
        start, end = float(r.get("start") or 0), float(r.get("end") or 0)
        dur = max(0.0, end - start)
        lens = [len(surf_join(g)) for g in groups]
        while len(groups) > 1:                      # a part shorter than MIN_SPLIT_SEC is joined to its neighbour
            tot = float(sum(lens)) or 1.0
            small = min(range(len(groups)), key=lambda x: lens[x])
            if dur * (lens[small] / tot) >= MIN_SPLIT_SEC:
                break
            lo = small - 1 if small > 0 else small
            groups[lo:lo + 2] = [groups[lo] + groups[lo + 1]]
            lens = [len(surf_join(g)) for g in groups]
        parts = [surf_join(g) for g in groups]
        new_norm = [norm(t) for p in parts for t in p.split() if norm(t)]
        if new_norm != old_norm:
            n_changed += 1
        elif " ".join(parts) != old_text.strip():
            n_style += 1
        if new_norm != old_norm:
            r.setdefault("asr_text", old_text)
            if str(r.get("arabic_text") or "").strip():
                report["arabic_stale"] += 1          # already translated from the old wording
        if len(parts) == 1:
            r["text"] = parts[0]
            _fix_words(r)
            new_rows.append(r)
            continue
        tot = float(sum(lens)) or 1.0                # several lines: the time is shared by how much text each has
        t = start
        for gi, p in enumerate(parts):
            nr = dict(r)
            if "arabic_text" in nr:
                nr["arabic_text"] = ""               # the old translation covered the whole sentence
            if gi > 0:
                nr["segment_id"] = make_id()
                nr["added"] = True
                nr.pop("asr_text", None)
            nr["text"] = p
            nr["words"] = [] if "words" in r else None
            if nr["words"] is None:
                nr.pop("words")
            t_end = end if gi == len(parts) - 1 else round(t + dur * (lens[gi] / tot), 2)
            nr["start"], nr["end"] = round(t, 2), round(t_end, 2)
            t = t_end
            new_rows.append(nr)
        n_split += len(parts) - 1
    # subtitle lines that fall inside the video but that nothing was heard for
    miss_units, cur = [], []
    for j in orphans:
        if cur and (cue[j] != cue[cur[-1]] or unit[j] != unit[cur[-1]] or j != cur[-1] + 1):
            miss_units.append(cur)
            cur = []
        cur.append(j)
    if cur:
        miss_units.append(cur)
    report["missed"] = len(miss_units)
    if add_missed and miss_units:
        new_rows, report["added"] = _add_missed(new_rows, miss_units, stream, pairs, rows, a_row, A, make_id)
    report["changed"], report["style"], report["split"] = n_changed, n_style, n_split
    if owner:
        c_lo, c_hi = min(cue[j] for j in owner), max(cue[j] for j in owner)
        report["ignored_cues"] = len(cues) - (c_hi - c_lo + 1)
        report["found_from"] = round(cues[c_lo]["start"], 1)
        report["found_to"] = round(cues[c_hi]["end"], 1)
    new_rows.sort(key=lambda x: (float(x.get("start") or 0), float(x.get("end") or 0)))
    return new_rows, report


def _fix_words(r):
    """Word times belong to the old words: keep them only while the number of words is unchanged."""
    if "words" in r and len(r.get("words") or []) != len(str(r.get("text") or "").split()):
        r["words"] = []


def _add_missed(new_rows, miss_units, stream, pairs, old_rows, a_row, A, make_id):
    """Rows for subtitle lines inside the video that the transcript never heard. The times come from the
    neighbouring words that DID match, so a subtitle on another film clock or at another speed still lands right."""
    first, count = {}, {}
    for i, r in enumerate(a_row):
        first.setdefault(r, i)
        count[r] = count.get(r, 0) + 1

    def a_time(i):
        r = old_rows[a_row[i]]
        s, e = float(r.get("start") or 0), float(r.get("end") or 0)
        return s + (e - s) * ((i - first[a_row[i]]) + 0.5) / count[a_row[i]]

    anchors = sorted(((j, stream["t"][j], a_time(i)) for i, j in pairs if A[i] == stream["norm"][j]))
    if not anchors:
        return new_rows, 0
    js = [a[0] for a in anchors]

    def to_a(j):
        p = bisect_left(js, j)
        lo = anchors[p - 1] if p > 0 else None
        hi = anchors[p] if p < len(anchors) else None
        tb = stream["t"][j]
        if lo and hi and hi[1] > lo[1]:
            return lo[2] + (tb - lo[1]) * (hi[2] - lo[2]) / (hi[1] - lo[1])
        ref = lo or hi
        return ref[2] + (tb - ref[1])

    out, added = list(new_rows), 0
    template = {k: v for k, v in (new_rows[0] if new_rows else {}).items() if k in ("speaker", "gender", "emotion", "speaker_id")}
    for unit_js in miss_units:
        ta0, ta1 = to_a(unit_js[0]) - 0.1, to_a(unit_js[-1]) + 0.3
        lo_edge, hi_edge = 0.0, 1e12                 # keep clear of the lines that are already there
        for r in out:
            s, e = float(r.get("start") or 0), float(r.get("end") or 0)
            if e <= ta0 + 0.05:
                lo_edge = max(lo_edge, e)
            elif s >= ta1 - 0.05:
                hi_edge = min(hi_edge, s)
            elif (s + e) / 2.0 <= (ta0 + ta1) / 2.0:
                lo_edge = max(lo_edge, e)
            else:
                hi_edge = min(hi_edge, s)
        ta0, ta1 = max(ta0, lo_edge), min(ta1, hi_edge)
        if ta1 - ta0 < MIN_SPLIT_SEC:
            continue
        nr = dict(template)
        nr.update({"segment_id": make_id(), "start": round(ta0, 2), "end": round(ta1, 2),
                   "text": " ".join(stream["surf"][j] for j in unit_js), "arabic_text": "", "locked": False, "added": True})
        out.append(nr)
        added += 1
    return out, added
