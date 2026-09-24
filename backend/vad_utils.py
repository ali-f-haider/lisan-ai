"""Voice-Activity-Detection cross-check for Whisper's word timestamps.

Whisper's own per-word timestamps come from a DTW alignment over
cross-attention weights -- they're not a direct measure of where a human
voice actually is. ffmpeg's silencedetect (see ffmpeg_utils.detect_silence_gaps)
gives a second, audio-measured signal, but it's a blunt volume threshold: on
a clip with background noise, music, or a quieter dialogue mix, it can't
tell "genuinely nobody talking" apart from "someone talking quietly under
something louder" -- both can sit under the same dB threshold.

Voice Activity Detection (VAD) is a better-suited third signal for exactly
that gap: a neural model trained to recognize human speech specifically,
not just loudness. faster-whisper already ships one internally (Silero
VAD, bundled with the package -- no extra download, no API key, no new
dependency), used to decide what to even send to the decoder in the first
place (vad_filter=True on the transcribe() call in whisper_service.py).
This module reuses that exact same bundled model to independently score a
stretch of audio for voice activity, so suspicious word timing can be
cross-checked against it two different ways (see the two flag_* functions
below).
"""
from faster_whisper.audio import decode_audio
from faster_whisper.vad import get_vad_model

_FRAME_SAMPLES = 512  # Silero's own chunk size -- 32ms at 16kHz
_SR = 16000


def speech_probability_curve(audio_path):
    """Returns (times, probs): a per-~32ms-frame speech-probability curve
    for the whole file, from the same VAD model faster-whisper uses
    internally. probs[i] is how confident the model is that frame i
    contains human speech (0.0-1.0); times[i] is that frame's start time
    in seconds. Raises on failure -- callers wrap this in try/except, same
    as detect_silence_gaps, since it's a best-effort enhancement that
    should never be allowed to break a transcription job."""
    audio = decode_audio(str(audio_path), sampling_rate=_SR)
    n = (len(audio) // _FRAME_SAMPLES) * _FRAME_SAMPLES
    if n == 0:
        return [], []
    audio = audio[:n]
    model = get_vad_model()
    probs = model(audio, num_samples=_FRAME_SAMPLES)
    times = [i * _FRAME_SAMPLES / _SR for i in range(len(probs))]
    return times, probs


def _mean_prob(times, probs, t0, t1):
    vals = [p for t, p in zip(times, probs) if t0 <= t < t1]
    if not vals:
        return None
    return float(sum(vals) / len(vals))


def _median(vals):
    if not vals:
        return None
    s = sorted(vals)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _file_baselines(result, times, probs):
    """Shared by both flag_* functions below: this file's own median
    confirmed-speech probability and median confirmed-pause probability,
    used as a per-file yardstick instead of one fixed global threshold
    (background noise level varies a lot between clips -- a fixed cutoff
    tuned for a clean recording can call an entire noisy clip "continuous
    speech" and flag nothing usefully; verified against a real noisy clip
    before shipping the first check that used this). Returns
    (speech_baseline, pause_baseline) or (None, None) if this file's own
    speech/pause levels aren't clearly separated enough to draw a
    meaningful line between them -- callers should skip flagging entirely
    rather than guess with an unreliable yardstick.

    Median, not mean, for both -- one short or imprecisely-bounded span (a
    breath right at a segment edge, a slightly-off boundary) can otherwise
    drag a plain average toward the other class and collapse the
    separation between them (seen in testing: a single atypical ~0.3s
    inter-segment gap was enough to do that with a mean).
    """
    speech_spans = []
    segment_bounds = []
    for seg in result:
        segment_bounds.append((seg["start"], seg["end"]))
        for w in seg.get("words", []):
            speech_spans.append((w["start"], w["end"]))
    if not speech_spans:
        return None, None

    def _median_over_spans(spans):
        vals = [m for m in (_mean_prob(times, probs, t0, t1) for t0, t1 in spans) if m is not None]
        return _median(vals)

    speech_baseline = _median_over_spans(speech_spans)
    if speech_baseline is None:
        return None, None

    segment_bounds.sort()
    # Only genuinely substantial inter-segment gaps count as "confirmed
    # pause" exemplars -- a short gap between adjacent segments is more
    # likely an imprecise boundary (a trailing breath, rounding) than a
    # clean silence, and would pollute the baseline rather than describe it.
    pause_spans = [
        (segment_bounds[i][1], segment_bounds[i + 1][0])
        for i in range(len(segment_bounds) - 1)
        if segment_bounds[i + 1][0] - segment_bounds[i][1] >= 0.5
    ]
    pause_baseline = _median_over_spans(pause_spans)
    if pause_baseline is None:
        # No substantial inter-segment pauses in this file to learn a real
        # baseline from (e.g. one continuous, unbroken line) -- fall back
        # to a conservative fraction of the speech level rather than skip.
        pause_baseline = speech_baseline * 0.3

    if speech_baseline - pause_baseline < 0.1:
        return None, None
    return speech_baseline, pause_baseline


def flag_suspect_word_gaps(result, audio_path, min_gap_sec=1.0):
    """For each segment, checks any internal word-to-word gap of at least
    min_gap_sec against real voice-activity data. A gap Whisper's word
    timestamps claim is empty, but where VAD finds speech-like probability
    comparable to confirmed speech elsewhere in THIS SAME file (not
    comparable to this file's own confirmed pauses), gets attached to that
    segment as seg["suspect_gaps"].

    This never auto-corrects anything -- we can't know the true timestamp,
    only that the claimed one looks inconsistent with real voice activity.
    It's a flag for a human to check before a mistimed word silently sets
    a wrong duration for a paid TTS generation, not a silent rewrite.
    """
    times, probs = speech_probability_curve(audio_path)
    if not times:
        return

    speech_baseline, pause_baseline = _file_baselines(result, times, probs)
    if speech_baseline is None:
        return
    # Biased toward the pause side (35%, not the midpoint) on purpose: the
    # two mistakes this cutoff can make aren't equally costly. Missing a
    # real bad gap lets a mistimed word silently set a wrong duration for a
    # paid TTS generation; flagging a genuinely-fine gap just asks for a
    # few extra seconds of a human's attention. Tested against a real
    # injected timestamp error (audio with real speech Whisper's word
    # timing skipped over) landing right at the midpoint -- biasing toward
    # catching it is the safer tradeoff.
    cutoff = pause_baseline + 0.35 * (speech_baseline - pause_baseline)

    for seg in result:
        words = seg.get("words", [])
        if len(words) < 2:
            continue
        suspects = []
        for k in range(len(words) - 1):
            gap_start, gap_end = words[k]["end"], words[k + 1]["start"]
            if gap_end - gap_start < min_gap_sec:
                continue
            m = _mean_prob(times, probs, gap_start, gap_end)
            if m is not None and m >= cutoff:
                w1 = (words[k].get("word") or "").strip()
                w2 = (words[k + 1].get("word") or "").strip()
                suspects.append({
                    "start": round(gap_start, 2), "end": round(gap_end, 2),
                    "probability": round(m, 3),
                    "reason": (f'Whisper\'s timing shows a {gap_end - gap_start:.1f}s gap here between '
                               f'"{w1}" and "{w2}", but voice-activity detection finds likely speech in '
                               f'that stretch -- the word timestamp may be wrong rather than this being '
                               f'a real pause. Worth checking before generating.'),
                })
        if suspects:
            seg["suspect_gaps"] = suspects


def flag_misaligned_words(result, audio_path, min_word_sec=0.12, min_speech_fraction=0.4, big_gap_sec=1.5):
    """Catches a different, rarer failure than flag_suspect_word_gaps above.
    That one looks for real speech hiding inside a gap Whisper claims is
    empty. This one looks for the mirror image: a word whose OWN claimed
    timestamp span shows almost no real voice activity at all -- meaning
    Whisper didn't just get the word's duration wrong, it anchored the word
    to roughly the wrong moment in the audio entirely. Found on a real
    clip: Whisper placed "both" at 2.59s-3.17s, but the word is actually
    spoken around 6.0s -- confirmed by direct listening, not just VAD.

    This kind of complete misplacement shows up specifically around long,
    acoustically ambiguous non-speech stretches (background score, engine
    noise, etc.) that confuse the model's attention-based alignment -- not
    on ordinary short dialogue pauses. So this ONLY evaluates words that
    sit immediately next to a gap of at least big_gap_sec (on either
    side), rather than checking every word in the transcript. That's a
    deliberate, tested design choice, not a shortcut: checking every word
    against this file's own VAD fraction was tried first and rejected --
    on real transcript data it flagged roughly a fifth of all words,
    almost entirely ordinary sentence-opening words the VAD model is
    simply slower to "warm up" on after a normal pause, not proof of
    misalignment. Restricting to words next to an unusually large gap (the
    biggest ordinary dialogue pause seen in real test data was 1.26s, well
    under the 1.5s default here) cut that same real data down to exactly
    the one genuine case, with nothing else flagged.

    Same flag-only philosophy as flag_suspect_word_gaps: never
    auto-corrects (we don't know the true timestamp, only that the claimed
    one looks wrong), and writes into the SAME seg["suspect_gaps"] field so
    the existing frontend warning indicator and auto-split partitioning
    both pick it up with no extra wiring.
    """
    times, probs = speech_probability_curve(audio_path)
    if not times:
        return

    speech_baseline, pause_baseline = _file_baselines(result, times, probs)
    if speech_baseline is None:
        return
    frame_cutoff = pause_baseline + 0.35 * (speech_baseline - pause_baseline)

    # Flatten every word across every segment, in time order, so the real
    # neighboring gap can be found even when it crosses a segment boundary
    # (the "both" case: the huge gap is between two different segments,
    # not inside one).
    flat = []
    for seg in result:
        for w in seg.get("words", []):
            flat.append((seg, w))
    flat.sort(key=lambda pair: pair[1]["start"])

    for idx, (seg, w) in enumerate(flat):
        w0, w1 = w["start"], w["end"]
        if w1 - w0 < min_word_sec:
            continue
        gap_before = w0 - flat[idx - 1][1]["end"] if idx > 0 else None
        gap_after = flat[idx + 1][1]["start"] - w1 if idx < len(flat) - 1 else None
        next_to_big_gap = (gap_before is not None and gap_before >= big_gap_sec) or \
                           (gap_after is not None and gap_after >= big_gap_sec)
        if not next_to_big_gap:
            continue
        vals = [p for t, p in zip(times, probs) if w0 <= t < w1]
        if len(vals) < 3:
            continue  # not enough frames in this word's own span to judge reliably
        frac = sum(1 for v in vals if v >= frame_cutoff) / len(vals)
        if frac < min_speech_fraction:
            word_text = (w.get("word") or "").strip()
            entry = {
                "start": round(w0, 2), "end": round(w1, 2),
                "probability": round(sum(vals) / len(vals), 3),
                "reason": (f'Whisper places "{word_text}" at {w0:.2f}s-{w1:.2f}s, right next to an unusually '
                           f'large timing gap, but voice-activity detection finds almost no real speech in '
                           f'that exact window -- the word may be anchored to the wrong point in the audio '
                           f'entirely, not just mistimed. Worth checking before generating.'),
            }
            seg.setdefault("suspect_gaps", []).append(entry)
