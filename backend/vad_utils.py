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
stretch of audio for voice activity, so a suspicious word-timing gap can be
cross-checked against it.
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

    Deliberately uses THIS FILE's own confirmed-speech and confirmed-pause
    stretches as the yardstick rather than one fixed global probability
    threshold -- background noise level varies a lot between clips, and a
    fixed cutoff tuned for a clean recording can call an entire noisy clip
    "continuous speech" and flag nothing usefully (verified against a real
    noisy clip before shipping this). Skips flagging entirely on a file
    where this file's own speech/pause levels aren't clearly separated,
    rather than guess with an unreliable yardstick.
    """
    times, probs = speech_probability_curve(audio_path)
    if not times:
        return

    speech_spans = []
    segment_bounds = []
    for seg in result:
        segment_bounds.append((seg["start"], seg["end"]))
        for w in seg.get("words", []):
            speech_spans.append((w["start"], w["end"]))
    if not speech_spans:
        return

    def _median(vals):
        if not vals:
            return None
        s = sorted(vals)
        mid = len(s) // 2
        return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0

    def _median_over_spans(spans):
        vals = [m for m in (_mean_prob(times, probs, t0, t1) for t0, t1 in spans) if m is not None]
        return _median(vals)

    # Median, not mean, for both baselines -- one short or imprecisely-
    # bounded span (a breath right at a segment edge, a slightly-off
    # boundary) can otherwise drag a plain average toward the other class
    # and collapse the separation between them (seen in testing: a single
    # atypical ~0.3s inter-segment gap was enough to do that with a mean).
    speech_baseline = _median_over_spans(speech_spans)
    if speech_baseline is None:
        return

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
        # This file's own speech and pause levels aren't clearly separated
        # enough to draw a meaningful line between them -- don't guess.
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
