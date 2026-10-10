"""Who speaks and who is spoken to, for a translation into Arabic (which marks gender and number; English does not).

* detect_genders(): the gender of each speaker. The person's own choice always wins; then what a listening check hears in the
  speaker's own voice; then the pitch. Each answer says where it came from and whether it is uncertain.
* build_context(): for the lines being translated, the speaker's gender, the neighbouring lines (who they say it to is
  usually the speaker of the line before or after) and the other speakers with their genders. The translator reads this.
* Nothing here changes a line. It never raises; a missing piece just leaves the translator with less to go on.
"""
import subprocess
import uuid
from pathlib import Path

GENDERS = ("male", "female")
CONTEXT_LINES = 3           # neighbouring lines shown before and after each line
MAX_CONTEXT_CHARS = 220     # of each neighbouring line


def clean(g):
    g = str(g or "").strip().lower()
    return g if g in GENDERS else ""


def _ffmpeg_cut(src, start, dur, out):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, float(start)):.3f}", "-t", f"{float(dur):.3f}",
                    "-i", str(src), "-vn", "-ar", "16000", "-ac", "1", str(out)], check=True, timeout=60)


def detect_genders(job_id, src, segments, chosen=None, *, work, key=None, cut=None, describe=None, max_listened=6):
    """{speaker name: {"gender": "male"|"female"|"", "source": "chosen"|"heard"|"pitch"|"", "uncertain": bool}}.

    segments: [{speaker, start, end}] (times in the file `src`); chosen: {name: gender} only what the person picked.
    describe(job_id, mp3_path) is the listening check (default: gemini_service.describe_speaker_voice with `key`)."""
    chosen = {str(k): clean(v) for k, v in (chosen or {}).items() if clean(v)}
    names = []
    for s in segments or []:
        n = str(s.get("speaker") or "")
        if n and n not in names:
            names.append(n)
    out = {n: {"gender": chosen[n], "source": "chosen", "uncertain": False} for n in names if n in chosen}
    todo = [n for n in names if n not in chosen]
    if not todo:
        return out
    for n in todo:
        out[n] = {"gender": "", "source": "", "uncertain": True}
    if src is None or not Path(str(src)).exists():
        return out
    try:
        import voice_match as vm
        import voice_match_service as svc
        if describe is None and key:
            import gemini_service
            describe = lambda jid, mp3: gemini_service.describe_speaker_voice(jid, mp3, key)
        cut = cut or _ffmpeg_cut
        work = Path(work)
        work.mkdir(parents=True, exist_ok=True)
        todo.sort(key=lambda n: -sum(float(s["end"]) - float(s["start"]) for s in segments if s.get("speaker") == n))
        asked = 0
        for n in todo:
            try:
                pieces = [(float(s["start"]), float(s["end"])) for s in segments if s.get("speaker") == n]
                x, got = svc._speaker_audio(src, pieces, cut, work, svc.PITCH_SECONDS)
                prof = vm.acoustic_profile(x, svc.SR) if got >= 1.0 else {}
                ai = None
                if describe is not None and got >= 2.0 and asked < max_listened:
                    asked += 1
                    wav, mp3 = work / f"{uuid.uuid4().hex}.wav", work / f"{uuid.uuid4().hex}.mp3"
                    try:
                        svc._write_wav(wav, x[: int(svc.LISTEN_SECONDS * svc.SR)])
                        cut(wav, 0, svc.LISTEN_SECONDS, mp3)
                        ai = describe(job_id, mp3)
                    except Exception:
                        ai = None
                    finally:
                        for p in (wav, mp3):
                            try:
                                p.unlink()
                            except Exception:
                                pass
                t = vm.speaker_traits(prof, ai)
                g, source = clean(t.get("gender")), t.get("gender_source") or ""
                pitch_g = clean(vm.guess_gender(prof.get("f0_median"))) if prof else ""
                uncertain = (source != "heard") or bool(ai and ai.get("uncertain"))
                if source == "heard" and pitch_g and pitch_g != g:
                    uncertain = True                       # the listener and the pitch disagree
                out[n] = {"gender": g, "source": source if g else "", "uncertain": uncertain if g else True}
            except Exception as ex:
                print(f"[gender] could not judge {n}: {ex}")
    except Exception as ex:
        print(f"[gender] detection skipped: {ex}")
    return out


def speaker_table(genders):
    """What the translator is told about each speaker: {name: "male"|"female"|"unknown"} plus how sure we are."""
    table = {}
    for name, info in (genders or {}).items():
        g = clean((info or {}).get("gender"))
        if not g:
            table[name] = "unknown"
        elif (info or {}).get("source") == "chosen":
            table[name] = f"{g} (certain: chosen by the user)"
        else:
            table[name] = f"{g} ({'probably' if (info or {}).get('uncertain') else 'heard'}, from the voice)"
    return table


def build_context(rows_all, ids, genders):
    """{segment_id: {"speaker_gender", "before": [...], "after": [...]}} for the lines `ids`, from ALL lines in time order.
    Each neighbour is {"speaker", "gender", "english"}. rows_all: [{segment_id, start, speaker, text}]."""
    order = sorted((r for r in rows_all or [] if str(r.get("text") or "").strip() or r.get("segment_id") in ids),
                   key=lambda r: float(r.get("start") or 0))
    pos = {r.get("segment_id"): i for i, r in enumerate(order)}

    def view(r):
        g = clean(((genders or {}).get(r.get("speaker")) or {}).get("gender"))
        return {"speaker": r.get("speaker"), "gender": g or "unknown", "english": str(r.get("text") or "").strip()[:MAX_CONTEXT_CHARS]}

    out = {}
    for sid in ids:
        i = pos.get(sid)
        if i is None:
            continue
        me = order[i]
        out[sid] = {"speaker_gender": clean(((genders or {}).get(me.get("speaker")) or {}).get("gender")) or "unknown",
                    "before": [view(r) for r in order[max(0, i - CONTEXT_LINES):i]],
                    "after": [view(r) for r in order[i + 1:i + 1 + CONTEXT_LINES]]}
    return out


def read_flags(item):
    """(addressee, gender_check) from one translated item; unknown or odd values become ("", False)."""
    addressee = str((item or {}).get("addressee") or "").strip().lower()
    if addressee not in ("male", "female", "group", "none", "unknown"):
        addressee = ""
    return addressee, (item or {}).get("gender_check") is True
