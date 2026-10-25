"""A free, local second opinion on who says a line: how high the voice is.

Why: the speaker check (speaker_vote.py) lets the dialogue, the listening request and the voice detector vote. When a man and a
boy talk, all three can agree on the wrong person for a short line ("How's your face?" given to the man) because they lean on the
same cues (turn-taking, the detector's clusters). The pitch of the voice is a different kind of evidence and costs nothing:
a man talks at about 90-140 Hz, a boy at about 220-300 Hz.

What it does, and only this:
  1. Measures the pitch of every line (the separated voices, the line's own time window). A line with less than MIN_VOICED_FRAMES of
     steady voice (0.15 s), or whose pitch jumps around (two voices, noise), is NOT measured and is left alone.
  2. Works out each speaker's usual pitch from their own measured lines (the median; a speaker needs MIN_PROFILE_LINES of them).
  3. Moves a line only when ALL of these hold: its pitch is OWN_FAR_ST semitones or more away from its speaker's usual pitch, it is
     within OTHER_NEAR_ST semitones of exactly one other speaker's usual pitch, the two speakers' usual pitches are at least
     SPEAKERS_APART_ST semitones apart (two voices that sound alike cannot be told apart this way), AND it lies at least
     RANGE_MARGIN_ST semitones outside everything its own speaker does in the rest of the video (the lowest and highest pitch of
     that speaker's other measured lines). A man's emphatic "Good." can be 9 semitones above his usual pitch; it is not a boy,
     because he reaches nearly that high elsewhere. A voice the speaker never makes anywhere else is what counts.
  4. One sentence is said by one voice: when a moved line is the end of a sentence that begins in a short unfinished line of the
     old speaker right before it (or the unfinished start of one that ends right after it), that fragment (at most
     MAX_FRAGMENT_WORDS words, within MAX_FRAGMENT_GAP seconds) moves with it, unless its own pitch says it is the old speaker's.
  5. Undoes everything when it would move more than MAX_MOVED_SHARE of the measured lines (then the measurement is not trusted).
Every moved line is marked "check speaker" for the user and listed in the job log with the numbers.

It never touches a line the user set by hand (manual_speaker), never adds or merges a speaker, never costs credits and never raises.
Switch off with LONGDUB_PITCH_CHECK=0.
"""
import math
import os
import re

ENABLED = os.environ.get("LONGDUB_PITCH_CHECK", "1").strip().lower() not in ("0", "false", "off", "no")

PITCH_MIN_HZ = 70.0
PITCH_MAX_HZ = 450.0
FRAME_SEC = 0.04
HOP_SEC = 0.01
VOICED_STRENGTH = 0.55          # normalised autocorrelation a frame needs to count as voiced
MIN_RUN_FRAMES = 5              # 50 ms of steady voice
RUN_JUMP = 0.25                 # inside a run a frame more than 25% from the run's median is dropped
WORK_SR = 16000
LEVEL_FLOOR = 0.003             # about -50 dBFS: below this a frame is silence, whatever its shape
LEVEL_REL = 0.12                # and a frame quieter than 12% of the line's loudest frame is not voice either

MIN_VOICED_FRAMES = 15          # 0.15 s of steady voice in a line before it counts
EDGE_SEC = 0.04                 # a line's window is widened by this on each side (the times in a transcript are rough); a run of
                                # voice shorter than MIN_RUN_FRAMES that leaks in from the next line is dropped by the run filter
MAX_SPREAD_ST = 6.0             # the middle half of a line's pitch may span at most this many semitones to be measured
MIXED_SPREAD_ST = 8.0           # ... and a line whose middle half spans more than this holds two different voices (or a very odd one)
MIN_PROFILE_LINES = 4           # lines a speaker needs before their usual pitch is trusted
OWN_FAR_ST = 8.0
OTHER_NEAR_ST = 4.0
SPEAKERS_APART_ST = 8.0
RANGE_MARGIN_ST = 3.0
MAX_MOVED_SHARE = 0.25
MAX_LISTED = 12
MAX_FRAGMENT_WORDS = 3          # an unfinished beginning/ending of a moved sentence that may follow it to the new speaker
MAX_FRAGMENT_GAP = 2.5          # seconds between the fragment and the moved line (the times in a transcript are rough)
_SENT_END = re.compile(r"[.!?؟…][\"'”’)\]]*\s*$")


def semitones(a, b):
    """Distance between two pitches in semitones (always >= 0)."""
    try:
        return abs(12.0 * math.log2(float(a) / float(b)))
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


# ------------------------------------------------------------------ measuring (numpy only)

def f0_frames(x, sr):
    """Pitch in Hz of every 10 ms frame of `x` (mono float array at `sr`), 0.0 where there is no steady voice."""
    import numpy as np
    x = np.asarray(x, dtype=np.float64)
    if sr != WORK_SR:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(int(sr), WORK_SR)
        x = resample_poly(x, WORK_SR // g, int(sr) // g)
        sr = WORK_SR
    n_win, n_hop = int(round(FRAME_SEC * sr)), int(round(HOP_SEC * sr))
    if x.size < n_win + n_hop:
        return np.zeros(0)
    n_frames = 1 + (x.size - n_win) // n_hop
    idx = np.arange(n_win)[None, :] + n_hop * np.arange(n_frames)[:, None]
    frames = x[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)
    win = np.hanning(n_win)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    framed = frames * win
    size = 1 << int(math.ceil(math.log2(2 * n_win)))
    spec = np.fft.rfft(framed, size, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, size, axis=1)[:, :n_win]
    wspec = np.fft.rfft(win, size)
    wac = np.fft.irfft(np.abs(wspec) ** 2, size)[:n_win]
    wac = wac / wac[0]
    r = ac / np.maximum(ac[:, :1], 1e-12) / np.maximum(wac[None, :], 1e-3)
    lo, hi = int(sr / PITCH_MAX_HZ), min(int(sr / PITCH_MIN_HZ) + 1, n_win - 2)
    seg = r[:, lo - 1:hi + 2]                       # one lag either side so a peak at the edge can be told from a slope
    inner = seg[:, 1:-1]
    peak = (inner > seg[:, :-2]) & (inner >= seg[:, 2:])
    best = np.where(peak, inner, -1.0)
    top = best.max(axis=1)
    # the first peak that is nearly as strong as the strongest one: avoids picking a lower octave (two periods)
    ok = peak & (inner >= 0.85 * top[:, None])
    first = ok.argmax(axis=1)
    strength = inner[np.arange(n_frames), first]
    lag = lo + first
    f0 = sr / lag
    loud = rms >= max(LEVEL_FLOOR, LEVEL_REL * float(rms.max() if rms.size else 0.0))
    voiced = loud & (top >= VOICED_STRENGTH) & (strength >= VOICED_STRENGTH * 0.9) & ok.any(axis=1)
    out = np.where(voiced, f0, 0.0)
    return _steady_runs(out)


def _steady_runs(f0):
    """Keep only runs of at least MIN_RUN_FRAMES voiced frames, and inside a run drop frames that jump away from the run's median."""
    import numpy as np
    out = np.zeros_like(f0)
    n, i = len(f0), 0
    while i < n:
        if f0[i] <= 0:
            i += 1
            continue
        j = i
        while j < n and f0[j] > 0:
            j += 1
        run = f0[i:j]
        if len(run) >= MIN_RUN_FRAMES:
            med = float(np.median(run))
            keep = np.abs(run / med - 1.0) <= RUN_JUMP
            if int(keep.sum()) >= MIN_RUN_FRAMES:
                out[i:j] = np.where(keep, run, 0.0)
        i = j
    return out


def line_pitch(x, sr):
    """(voiced frames, median Hz, spread in semitones of the middle half) of one line's samples; (0, 0.0, 0.0) when nothing steady."""
    import numpy as np
    f0 = f0_frames(x, sr)
    v = f0[f0 > 0]
    if v.size == 0:
        return 0, 0.0, 0.0
    return int(v.size), float(np.median(v)), semitones(np.percentile(v, 75), np.percentile(v, 25))


def measure(rows, wav_path):
    """{segment_id: (voiced frames, median Hz, spread semitones)} for the rows that have enough steady voice. Reads only each line's
    own samples from the file, so a long video is never loaded whole."""
    return measure_all(rows, wav_path)[0]


def measure_all(rows, wav_path):
    """(measured, mixed): `measured` as measure(); `mixed` is {segment_id: spread} for lines with enough voice whose pitch jumps between
    two very different ranges (more than MIXED_SPREAD_ST semitones in the middle half): those are not measured but are worth a look."""
    import soundfile as sf
    out, mixed = {}, {}
    with sf.SoundFile(str(wav_path)) as f:
        sr = f.samplerate
        for r in rows:
            try:
                a, b = float(r["start"]) - EDGE_SEC, float(r["end"]) + EDGE_SEC
                if b - a < 0.2:
                    continue
                f.seek(max(0, int(a * sr)))
                x = f.read(int((b - a) * sr), dtype="float32", always_2d=True)
                if x.size == 0:
                    continue
                n, med, spread = line_pitch(x.mean(axis=1), sr)
                if n >= MIN_VOICED_FRAMES and spread <= MAX_SPREAD_ST:
                    out[r["segment_id"]] = (n, med, spread)
                elif n >= MIN_VOICED_FRAMES and spread > MIXED_SPREAD_ST:
                    mixed[r["segment_id"]] = spread
            except Exception:
                continue
    return out, mixed


# ------------------------------------------------------------------ deciding (pure)

def _median(v):
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def profiles(rows, meas):
    """{speaker: usual pitch in Hz} from each speaker's own measured lines; a speaker with too few lines has none."""
    by = {}
    for r in rows:
        m = meas.get(r.get("segment_id"))
        if m:
            by.setdefault(r.get("speaker"), []).append(m[1])
    return {s: _median(v) for s, v in by.items() if len(v) >= MIN_PROFILE_LINES}


def _beyond(hz, lo, hi):
    """How many semitones `hz` lies outside [lo, hi] (0 when inside)."""
    if hz > hi:
        return semitones(hz, hi)
    if hz < lo:
        return semitones(lo, hz)
    return 0.0


def ranges(rows, meas, prof):
    """{speaker: (lowest, highest Hz)} over the speaker's measured lines that are NOT themselves far from their usual pitch
    (a line that might be someone else's must not widen the range it is judged against)."""
    by = {}
    for r in rows:
        m = meas.get(r.get("segment_id"))
        sp = r.get("speaker")
        if m and sp in prof and semitones(m[1], prof[sp]) < OWN_FAR_ST:
            by.setdefault(sp, []).append(m[1])
    return {sp: (min(v), max(v)) for sp, v in by.items() if len(v) >= MIN_PROFILE_LINES}


def decide(rows, meas):
    """The lines the pitch says belong to someone else: [{"sid", "from", "to", "hz"}]. Pure; changes nothing."""
    prof = profiles(rows, meas)
    moves = []
    if len(prof) < 2:
        return moves, prof
    span = ranges(rows, meas, prof)
    for r in rows:
        sid, own = r.get("segment_id"), r.get("speaker")
        m = meas.get(sid)
        if not m or own not in prof or r.get("manual_speaker"):
            continue
        hz = m[1]
        if semitones(hz, prof[own]) < OWN_FAR_ST:
            continue
        if own not in span or _beyond(hz, *span[own]) < RANGE_MARGIN_ST:
            continue                      # something this speaker does elsewhere in the video, or no range to judge by
        near = [s for s in prof if s != own and semitones(hz, prof[s]) <= OTHER_NEAR_ST]
        near = [s for s in near if semitones(prof[s], prof[own]) >= SPEAKERS_APART_ST]
        if len(near) == 1:
            moves.append({"sid": sid, "from": own, "to": near[0], "hz": hz})
    return moves, prof


def _words(text):
    return len(str(text or "").split())


def with_fragments(rows, moves, meas, prof):
    """The unfinished neighbours that go with a moved line (see the module note, point 4): [{"sid", "from", "to", "with"}]. Pure."""
    idx = {r.get("segment_id"): i for i, r in enumerate(rows)}
    taken = {m["sid"] for m in moves}
    extra = []
    for m in moves:
        i = idx.get(m["sid"])
        if i is None:
            continue
        moved_row = rows[i]
        for j, before in ((i - 1, True), (i + 1, False)):
            if not 0 <= j < len(rows):
                continue
            frag = rows[j]
            sid = frag.get("segment_id")
            if sid in taken or frag.get("speaker") != m["from"] or frag.get("manual_speaker"):
                continue
            if not 0 < _words(frag.get("text")) <= MAX_FRAGMENT_WORDS:
                continue
            try:
                if before:
                    gap, unfinished = float(moved_row["start"]) - float(frag["end"]), not _SENT_END.search(str(frag.get("text") or ""))
                else:
                    gap, unfinished = float(frag["start"]) - float(moved_row["end"]), not _SENT_END.search(str(moved_row.get("text") or ""))
            except (KeyError, TypeError, ValueError):
                continue
            if not unfinished or gap > MAX_FRAGMENT_GAP or gap < -0.5:
                continue
            fm = meas.get(sid)
            if fm and m["from"] in prof and m["to"] in prof and semitones(fm[1], prof[m["from"]]) < semitones(fm[1], prof[m["to"]]):
                continue                      # its own voice says it really is the old speaker's
            taken.add(sid)
            extra.append({"sid": sid, "from": m["from"], "to": m["to"], "with": m["sid"]})
    return extra


def _short(name):
    import re
    m = re.fullmatch(r"Speaker ([0-9]+)", str(name or ""))
    return "S" + m.group(1) if m else str(name or "?")[:8]


def _clock(t):
    m, s = divmod(max(0.0, float(t)), 60.0)
    return "%d:%04.1f" % (int(m), s)


def run(rows, wav_path):
    """Measure, decide, and move the lines in `rows` (dicts with segment_id, start, end, speaker, text). Returns
    {"status", "moved", "text"}: status is "ok" (looked at the lines), "off", "skipped" or "error"; text is the one-line job log entry.
    Never raises."""
    res = {"status": "off", "moved": 0, "text": "switched off (LONGDUB_PITCH_CHECK=0)"}
    if not ENABLED:
        return res
    try:
        rows = [r for r in rows if isinstance(r, dict) and r.get("segment_id") is not None]
        if not wav_path or not os.path.exists(str(wav_path)):
            return {"status": "skipped", "moved": 0, "text": "no voice recording to measure"}
        if len({r.get("speaker") for r in rows}) < 2:
            return {"status": "skipped", "moved": 0, "text": "only one speaker, nothing to tell apart"}
        meas, mixed = measure_all(rows, wav_path)
        moves, prof = decide(rows, meas)
        head = "measured %d of %d lines (at least %.2f s of steady voice each)" % (len(meas), len(rows), MIN_VOICED_FRAMES * HOP_SEC)
        if len(prof) < 2:
            return {"status": "skipped", "moved": 0, "text": head + "; fewer than two speakers have %d measured lines, so no usual pitch to compare with" % MIN_PROFILE_LINES}
        usual = ", ".join("%s %d Hz" % (_short(s), round(hz)) for s, hz in sorted(prof.items(), key=lambda kv: str(kv[0])))
        if moves and len(moves) > max(2, MAX_MOVED_SHARE * len(meas)):
            return {"status": "skipped", "moved": 0, "text": "%s; usual pitch: %s; it disagreed with %d of them, too many to trust, nothing changed" % (head, usual, len(moves))}
        by_id = {r["segment_id"]: r for r in rows}
        extra = with_fragments(rows, moves, meas, prof)
        moved_ids = {m["sid"] for m in moves + extra}
        flagged = []
        for r in rows:
            sid = r["segment_id"]
            if sid in mixed and sid not in moved_ids and not r.get("manual_speaker"):
                sv = r.get("speaker_vote") if isinstance(r.get("speaker_vote"), dict) else {}
                r["speaker_vote"] = dict(sv, badge=True, reasons=list(dict.fromkeys(list(sv.get("reasons") or []) + ["voters_split"])),
                                         support=sv.get("support", 1), present=sv.get("present", 4), source=sv.get("source", "pitch"))
                flagged.append(sid)
        listed = []
        for mv in moves + extra:
            r = by_id[mv["sid"]]
            r["speaker"] = mv["to"]
            r["speaker_vote"] = {"source": "pitch", "support": 1, "present": 4, "badge": True, "reasons": ["voters_split"]}
            txt = " ".join(str(r.get("text") or "").split())
            txt = txt if len(txt) <= 24 else txt[:23].rstrip() + "…"
            how = "(%d Hz)" % round(mv["hz"]) if "hz" in mv else "(goes with %s)" % mv["with"]
            listed.append('%s %s "%s" %s->%s %s' % (mv["sid"], _clock(r["start"]), txt.replace('"', "'"), _short(mv["from"]), _short(mv["to"]), how))
        text = "%s; usual pitch: %s; %d line(s) moved%s" % (head, usual, len(moves), (" + %d unfinished neighbour(s) with them" % len(extra)) if extra else "")
        if listed:
            text += ": " + " | ".join(listed[:MAX_LISTED]) + (" | ..." if len(listed) > MAX_LISTED else "")
        if flagged:
            text += "; %d line(s) with two different pitches marked 'check speaker' (nothing moved): %s" % (len(flagged), ", ".join(flagged[:MAX_LISTED]))
        return {"status": "ok", "moved": len(moves) + len(extra), "flagged": len(flagged), "text": text[:1400]}
    except Exception as ex:
        return {"status": "error", "moved": 0, "text": "could not run (%s)" % type(ex).__name__}
