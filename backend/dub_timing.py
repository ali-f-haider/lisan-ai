"""Where every dubbed line plays, so that no line is ever cut short (long dub).

Pure logic, no I/O. A generated Arabic line is often longer than the English one
it replaces. Cutting its end off is the worst answer, so the answers are tried
in this order, the gentlest first:

  1. shift     a line may start a little early or late (it uses the silence before
               it, and it pushes the lines after it a little, until a pause takes
               the push up)
  2. rephrase  a shorter wording of the same sentence (the caller does that)
  3. speed     a little faster than the usual limit (the caller does that)
  4. widen     the shift is allowed to be larger
  5. overlap   the last words may fade out under the first words of the next line
  6. drift     the lines after it may be late by more (up to DRIFT_LAG), until the next pause

Only if all six fail does the old behaviour (cutting the end) remain.
Every step that changes a line is reported back, so the job log can say so.
"""

GAP = 0.05                 # silence kept between the end of a line and the next one
LEAD = 0.30                # first try: a line may start this much earlier than the original ...
LAG = 0.50                 # ... or this much later
WIDE_LEAD = 0.60           # when that is not enough
WIDE_LAG = 1.20
DRIFT_LAG = 2.50           # the very last resort: a long run of lines with no pause may end up this late
MAX_OVERLAP = 0.40         # the tail of a line that may play under the start of the next one
EPS = 1e-6


def solve(lines, total, lead=LEAD, lag=LAG, gap=GAP):
    """lines: [{"start": original start, "dur": length of the generated line, "overlap": seconds of its tail
    that may play under the next line (default 0)}] sorted by start.
    Returns {"starts": [...], "ok": bool, "overrun": seconds, "candidates": [indexes], "first_bad": index or None}.
    A line is placed as close to its original start as the lines around it allow, never earlier than
    `lead` before it and never later than `lag` after it. When that cannot be done for every line, "ok" is
    False and "candidates" lists the lines whose shortening would help (the tight block around the first
    line that does not fit)."""
    n = len(lines)
    if n == 0:
        return {"starts": [], "ok": True, "overrun": 0.0, "candidates": [], "first_bad": None}
    s = [float(x["start"]) for x in lines]
    dur = [float(x["dur"]) for x in lines]
    eff = [max(0.0, dur[i] - float(lines[i].get("overlap") or 0.0)) for i in range(n)]
    # latest start of each line: it may be late by `lag`, and the lines after it must still fit
    U = [0.0] * n
    U[n - 1] = min(s[n - 1] + lag, float(total) - dur[n - 1])
    for i in range(n - 2, -1, -1):
        U[i] = min(s[i] + lag, U[i + 1] - gap - eff[i])
    t = [0.0] * n
    prev_end = None
    for i in range(n):
        floor = max(s[i] - lead, 0.0)
        if prev_end is not None:
            floor = max(floor, prev_end + gap)
        want = s[i] if prev_end is None else max(s[i], prev_end + gap)
        t[i] = max(floor, min(U[i], want))
        prev_end = t[i] + eff[i]
    over = [max(0.0, t[i] - U[i]) for i in range(n)]
    bad = [i for i in range(n) if over[i] > 1e-3]
    if not bad:
        return {"starts": t, "ok": True, "overrun": 0.0, "candidates": [], "first_bad": None}
    b = bad[0]
    block = set([b])
    j = b                                    # to the left: lines that are pressed against the one before them
    while j > 0 and t[j] <= t[j - 1] + eff[j - 1] + gap + 1e-3:
        j -= 1
        block.add(j)
    j = b                                    # to the right: lines whose latest start is set by the line after them
    while j + 1 < n and abs(U[j] - (U[j + 1] - gap - eff[j])) < 1e-6:
        j += 1
        block.add(j)
    if j + 1 < n:
        block.add(j + 1)
    return {"starts": t, "ok": False, "overrun": over[b], "candidates": sorted(block), "first_bad": b}


def make_plan(lines, total, reduce_cb=None, lead=LEAD, lag=LAG, wide_lead=WIDE_LEAD, wide_lag=WIDE_LAG,
              max_overlap=MAX_OVERLAP, drift_lag=DRIFT_LAG, gap=GAP, max_steps=400):
    """lines: [{"seg": id, "start": s, "dur": d}] (any order). reduce_cb(stage, seg, target_dur) is asked, for
    stage "rephrase" and then "speed", to make that line shorter than target_dur if it can; it returns the new
    length in seconds, or None when it could not. It may be None (only shifting and overlapping are then used).
    Returns {"starts": {seg: start}, "durs": {seg: dur}, "overlap": {seg: seconds}, "ok": bool,
    "rephrased": [seg], "sped": [seg], "widened": bool, "drifted": bool, "overlapped": [seg], "moved": n, "max_move": seconds}."""
    L = [{"seg": x["seg"], "start": float(x["start"]), "dur": float(x["dur"]), "overlap": 0.0}
         for x in sorted(lines, key=lambda z: float(z["start"]))]
    cur = {"lead": lead, "lag": lag}
    rep = {"rephrased": [], "sped": [], "widened": False, "drifted": False, "overlapped": []}

    def run():
        return solve(L, total, cur["lead"], cur["lag"], gap)

    res = run()
    for stage in ("rephrase", "speed", "widen", "overlap", "drift"):
        if res["ok"]:
            break
        if stage == "widen":
            cur["lead"], cur["lag"] = max(wide_lead, lead), max(wide_lag, lag)
            rep["widened"] = True
            res = run()
            continue
        if stage == "drift":
            cur["lag"] = max(drift_lag, cur["lag"])
            rep["drifted"] = True
            res = run()
            continue
        tried = set()
        for _ in range(max_steps):
            if res["ok"]:
                break
            cands = [k for k in res["candidates"] if L[k]["seg"] not in tried]
            if stage == "overlap":
                cands = [k for k in cands if L[k]["overlap"] < max_overlap - 0.005]
            if not cands:
                break
            k = max(cands, key=lambda z: L[z]["dur"])
            line = L[k]
            if stage == "overlap":
                give = min(max_overlap - line["overlap"], res["overrun"] + 0.02)
                line["overlap"] += give
                if line["seg"] not in rep["overlapped"]:
                    rep["overlapped"].append(line["seg"])
            else:
                tried.add(line["seg"])
                if reduce_cb is None:
                    continue
                target = max(line["dur"] - res["overrun"] - 0.04, line["dur"] * 0.55)
                new = reduce_cb(stage, line["seg"], target)
                if new is not None and 0 < new < line["dur"] - 0.03:
                    line["dur"] = float(new)
                    key = "rephrased" if stage == "rephrase" else "sped"
                    if line["seg"] not in rep[key]:
                        rep[key].append(line["seg"])
            res = run()
    moves = [abs(res["starts"][i] - L[i]["start"]) for i in range(len(L))]
    return {"starts": {L[i]["seg"]: res["starts"][i] for i in range(len(L))},
            "durs": {x["seg"]: x["dur"] for x in L},
            "overlap": {x["seg"]: x["overlap"] for x in L if x["overlap"] > 0},
            "ok": res["ok"], "rephrased": rep["rephrased"], "sped": rep["sped"], "widened": rep["widened"], "drifted": rep["drifted"],
            "overlapped": rep["overlapped"], "moved": sum(1 for m in moves if m > 0.02),
            "max_move": round(max(moves) if moves else 0.0, 2)}
