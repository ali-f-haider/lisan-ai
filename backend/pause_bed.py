"""Pauses and steady beds.

The separated background is a rewritten version of the original recording. Two things go wrong with a quiet, steady background
(a room tone, an electrical buzz, an engine) when somebody speaks over it:

1. Where nobody speaks, the ORIGINAL recording is the real background. `pause_mask` finds those stretches from the separated
   voices (not from the transcript: a transcript segment often covers the pauses between its words).
2. Under the speech the separator takes part of the buzz away together with the voice, so the buzz is loud in every pause and
   weaker under every word: an audible pulse that follows the original speech ("ghost voice before his words"). `bed_floor`
   puts back what is missing, with new noise that has the spectrum of the real pauses, so the background never drops more
   than a few dB under the speech. Only for a steady background (checked), never for music, a battle or a crowd.
Nothing here raises; on any problem the caller keeps what it had."""
import os
import wave
from pathlib import Path

import numpy as np
from scipy.ndimage import maximum_filter1d
from scipy.signal import istft, stft

import music_fill

RATE = music_fill.RATE
FRAME = 441                      # 10 ms
PAD_SEC = 0.25                   # how far from any voice the original sound is used
FADE_SEC = 0.15
VOICE_LOCAL_DB = -45.0           # a frame is voice when the separated voices are louder than this under their local peak...
VOICE_GLOBAL_DB = -60.0          # ...or under their loudest moment of all (a stem silence is far lower than both)
VOICE_FLOOR_DB = -85.0
CONSISTENT_DB = 12.0             # the original may be this much louder than the separated background in a pause (a quiet room tone is taken
                                 # away in part by the separator); much louder than that means something is there the stem did not find
DIRTY_SHARE = 0.5                # the separated voices are "dirty" when they sound like voice in more than this share of the time nobody speaks
DIRTY_GAP_DB = 10.0               # ...and what the voices hold in those stretches is at least this much quieter than in the speech
DIRTY_STEADY_MAX_DB = 4.5        # a crowd / a restaurant murmurs (about 3 dB of variation); music with a beat is 10 and more
FLOOR_DB = float(os.environ.get('DUB_BG_FLOOR_DB', '-4'))   # under the speech the steady background is at least this far under its pause level
BED_MIN_POOL_SEC = 1.2
CROWD_BED_DB = float(os.environ.get('DUB_CROWD_BED_DB', '-3'))   # a place with life in it (restaurant, street): under speech it is held this far under its pause level
CROWD_POOL_MAX_SEC = 300.0       # real sound of the pauses that may be laid under the speech (every piece is used once before any is repeated)
CROWD_GRAIN_SEC = 2.0
CROWD_FADE_SEC = 0.4
CROWD_EVENT_DB = 4.0             # a piece this much louder than the usual one is an event (a crash, a shout): it is not laid under the speech
CROWD_QUIET_DB = -10.0           # ...and a piece this much quieter is a dropout
CROWD_REF_SEC = 10.0             # the level of the pauses near a stretch of speech: the middle of the pauses within this many seconds
BED_POOL_MAX_SEC = 40.0
BED_MIN_RUN_SEC = 0.4
BED_MIN_DB = -80.0
N_FFT = 4096
SYNTH_CAL_DB = 8.9               # what noise added to the cells of an overlapping STFT loses on the way back (measured)
HOP = 1024
BLOCK_SEC = 30.0
CTX_SEC = 1.0


def _mono_db(pcm, frames):
    out = np.empty(frames)
    for i in range(0, frames, 6000):
        j = min(frames, i + 6000)
        blk = pcm[i * FRAME:j * FRAME].astype(np.float32) / 32768.0
        out[i:j] = 10.0 * np.log10((blk ** 2).reshape(j - i, FRAME, -1).mean(axis=(1, 2)) + 1e-12)
    return out


def _dilate(mask, frames):
    return np.convolve(mask.astype(np.float32), np.ones(2 * frames + 1), mode='same') > 0


def _smooth(x, frames):
    """Moving average over `frames` values, always exactly one value per input value. A stretch shorter than the window (the last block of
    a file) is averaged over the values it has, so it is neither cut nor lowered by the missing ones."""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n == 0 or frames <= 1:
        return x.copy()
    start = (frames - 1) // 2
    if n >= frames:
        return np.convolve(x, np.ones(frames) / frames, mode='full')[start:start + n]
    return (np.convolve(x, np.ones(frames), mode='full')[start:start + n] / np.convolve(np.ones(n), np.ones(frames), mode='full')[start:start + n])


def speech_frames(base_pcm, orig_pcm, vocal_pcm, spans, n, info=None):
    """True per 10 ms frame where the original must NOT be used (somebody speaks, or something is there the separator did not
    explain). `base_pcm` is the separated background as it came out of the separator.
    A separator sometimes files the whole scene (a restaurant: the crowd, the dishes, the cutlery) under "voices". Then the voices
    sound like voice all the time and no pause would ever be found. Such a stem is recognised by being "voice" in most of the time
    nobody speaks according to the transcript; then only the transcribed words (and their margins) count as speech.
    `info` (a dict) gets "dirty": True in that case."""
    nf = n // FRAME
    vl = _mono_db(vocal_pcm, nf)
    thr = max(float(vl.max()) + VOICE_GLOBAL_DB, VOICE_FLOOR_DB)
    local = maximum_filter1d(vl, size=1001, mode='nearest') + VOICE_LOCAL_DB
    voice = vl > np.maximum(local, thr)
    voice = _dilate(voice, int(PAD_SEC * 100))
    ol, bl = _smooth(_mono_db(orig_pcm, nf), 10), _smooth(_mono_db(base_pcm, nf), 10)
    consistent = ol <= bl + CONSISTENT_DB
    inside = np.zeros(nf, dtype=bool)
    for a, b in spans:
        inside[max(0, int((a - PAD_SEC) * 100)):min(nf, int((b + PAD_SEC) * 100) + 1)] = True
    outside = ~inside
    # ...and what the voices hold there is a murmur, clearly quieter than the speech: when it is as loud as the speech, the transcript
    # simply missed people talking, and the original must not be laid in there.
    if (spans and int(outside.sum()) >= 300 and float(voice[outside].mean()) > DIRTY_SHARE and inside.any()
            and float(np.percentile(vl[outside], 90)) <= float(np.percentile(vl[inside], 90)) - DIRTY_GAP_DB):
        if info is not None:
            info['dirty'] = True
        return inside
    return voice | (inside & ~consistent)


def _weights(speech):
    """1 where the original is used, 0 under speech, fading over FADE_SEC (a fade never reaches into the speech margin)."""
    w = (~speech).astype(np.float32)
    k = max(1, int(FADE_SEC * 100))
    return np.minimum(w, _smooth(w, k)).astype(np.float32)


def _open(src, raw):
    music_fill._to_pcm(src, raw)
    n = Path(raw).stat().st_size // 4
    return np.memmap(raw, dtype='<i2', mode='r', shape=(n, 2)) if n else None


def _close(pcms):
    for p in pcms:
        if p is not None:
            try:
                p._mmap.close()
            except Exception:
                pass


def _same_recording(base, orig, voc, n):
    mid, half = n // 2, min(n, 40 * RATE) // 2
    s0, s1 = max(0, mid - half), min(n, mid + half)
    om = orig[s0:s1].astype(np.float32).mean(axis=1)
    sm = base[s0:s1].astype(np.float32).mean(axis=1) + voc[s0:s1].astype(np.float32).mean(axis=1)
    return om.std() > 1e-3 and sm.std() > 1e-3 and float(np.corrcoef(om, sm)[0, 1]) >= 0.8


def original_in_pauses(base, original, vocals, spans, out_path, log=None, mask_base=None):
    """`out_path` = `base` with the original recording laid in wherever nobody speaks. mask_base = the separated background
    the pauses are judged against (default `base`; a muted or repaired track is not a fair judge). Returns {"ok", "reason", "share"}."""
    info = {'ok': False, 'reason': '', 'share': 0.0}
    out_path = Path(out_path)
    srcs = [base, original, vocals, mask_base if mask_base is not None else base]
    raws = [out_path.with_name(out_path.name + f'.pb{i}.pcm') for i in range(4)]
    pcms = []
    part = out_path.with_name(out_path.name + '.part.wav')
    try:
        if not original or not Path(original).exists() or not spans or not Path(base).exists():
            info['reason'] = 'no original recording to use'
            return info
        if vocals is None or not Path(vocals).exists():
            info['reason'] = 'no separated voices to check the original against'
            return info
        for s, r in zip(srcs, raws):
            pcms.append(_open(s, r))
        if any(p is None for p in pcms):
            info['reason'] = 'an audio file is empty'
            return info
        b, o, v, m = pcms
        n = len(b)
        if any(abs(len(x) - n) > RATE // 4 for x in (o, v, m)):
            info['reason'] = 'the original recording has another length than the separated background'
            return info
        n = min(len(b), len(o), len(v), len(m))
        if not _same_recording(m, o, v, n):
            info['reason'] = 'the original recording does not match the separated sound'
            return info
        sinfo = {}
        speech = speech_frames(m, o, v, spans, n, sinfo)
        w = _weights(speech)
        info['share'] = round(float(w.mean()), 3)
        info['dirty'] = bool(sinfo.get('dirty'))
        win = 30 * 100                                  # the share of pauses per 30 s: a log shows where the original could not be used
        info['by_30s'] = [int(round(100 * float(w[i:i + win].mean()))) for i in range(0, len(w), win)]
        if info['share'] < 0.02:
            info['reason'] = 'no pauses to use'
            return info
        nf = n // FRAME
        centers = (np.arange(nf) + 0.5) * FRAME
        with wave.open(str(part), 'wb') as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(RATE)
            step = 30 * RATE
            for a in range(0, n, step):
                c = min(n, a + step)
                wt = np.interp(np.arange(a, c), centers, w)[:, None].astype(np.float32)
                mixed = b[a:c].astype(np.float32) * (1.0 - wt) + o[a:c].astype(np.float32) * wt
                out.writeframes(np.clip(np.rint(mixed), -32768, 32767).astype('<i2').tobytes())
        part.replace(out_path)
        info['ok'] = True
        info['reason'] = f"the original sound is used in the pauses ({info['share'] * 100:.0f}% of the time)"
        return info
    except Exception as ex:
        info['reason'] = f'not used ({str(ex)[:160]})'
        if log:
            log('original in pauses: ' + info['reason'])
        return info
    finally:
        _close(pcms)
        for p in raws + [part]:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass


def _pool(orig, free, n, max_sec=BED_POOL_MAX_SEC):
    """Clean stretches of the original (pauses, at least BED_MIN_RUN_SEC long, trimmed at the edges), at most `max_sec` in all."""
    nf = n // FRAME
    segs, total, i = [], 0.0, 0
    trim = 5
    runs = []
    while i < nf:
        if free[i]:
            j = i
            while j < nf and free[j]:
                j += 1
            if (j - i - 2 * trim) * FRAME / RATE >= BED_MIN_RUN_SEC:
                runs.append((i + trim, j - trim))
            i = j
        else:
            i += 1
    runs.sort(key=lambda r: r[0] - r[1])             # longest first
    limit = int(max_sec * RATE)
    for a, c in runs:
        room = limit - int(round(total * RATE))
        if room < FRAME:
            break
        c = min(c, a + room // FRAME)                    # one long pause never takes more than what is left of the allowance (cut BEFORE converting)
        seg = orig[a * FRAME:c * FRAME].astype(np.float32) / 32768.0
        segs.append(seg)
        total += len(seg) / RATE
    return segs


def _pool_power(segs):
    """Per channel and frequency bin: the power of the pool, robust against a blip (mean limited by median / ln 2)."""
    win = np.hanning(N_FFT).astype(np.float32)
    out = []
    for ch in range(2):
        pw = []
        for s in segs:
            for k in range(0, len(s) - N_FFT + 1, HOP):
                pw.append(np.abs(np.fft.rfft(s[k:k + N_FFT, ch] * win)) ** 2)
        pw = np.stack(pw)
        out.append(np.minimum(pw.mean(axis=0), np.median(pw, axis=0) / np.log(2.0)))
    return np.stack(out)


class _Pieces:
    """An endless stream of REAL pieces of the pauses (a place with life in it: people, dishes, traffic). The pool is cut into pieces of
    CROWD_GRAIN_SEC and shuffled; every piece is used once before any is used again, the pieces are blended with an equal-power
    cross-fade, and a piece that is an event (a crash, a shout) or a dropout is left out. Nothing is made up."""

    def __init__(self, segs, seed=7):
        self.rng = np.random.default_rng(seed)
        g, f = int(CROWD_GRAIN_SEC * RATE), int(CROWD_FADE_SEC * RATE)
        self.fade = f
        grains = []
        for s in segs:
            off = int(self.rng.integers(0, max(1, min(g, len(s) - g + 1)))) if len(s) > g else 0
            for k in range(off, len(s) - g + 1, g - f):
                grains.append(s[k:k + g])
            if not grains and len(s) >= 2 * f:
                grains.append(s)
        pw = np.array([float(np.mean(x ** 2)) + 1e-14 for x in grains]) if grains else np.array([])
        self.power = 0.0
        self.grains = []
        if len(pw):
            db = 10.0 * np.log10(pw)
            med = float(np.median(db))
            keep = [i for i in range(len(grains)) if med + CROWD_QUIET_DB <= db[i] <= med + CROWD_EVENT_DB]
            self.grains = [grains[i] * np.float32(10.0 ** ((med - db[i]) / 20.0)) for i in keep]      # all at the usual level
            self.power = 10.0 ** (med / 10.0)
        self.order = []
        self.last = -1
        self.buf = np.zeros((0, 2), dtype=np.float32)

    def seconds(self):
        return sum(len(x) for x in self.grains) / RATE

    def _next(self):
        if not self.order:
            self.order = [int(i) for i in self.rng.permutation(len(self.grains))]
            if len(self.order) > 1 and self.order[0] == self.last:
                self.order.append(self.order.pop(0))
        k = self.order.pop(0)
        self.last = k
        return self.grains[k]

    def take(self, m):
        f = self.fade
        while len(self.buf) < m:
            g = self._next()
            if len(self.buf) >= f and len(g) > f:
                t = np.linspace(0.0, np.pi / 2, f, dtype=np.float32)[:, None]
                self.buf[-f:] = self.buf[-f:] * np.cos(t) + g[:f] * np.sin(t)
                self.buf = np.concatenate([self.buf, g[f:]])
            else:
                self.buf = np.concatenate([self.buf, g])
        out, self.buf = self.buf[:m], self.buf[m:]
        return out


def _pause_reference_db(o, free, n):
    """Per 10 ms frame: the level (dB re full scale, mean power, events left out) of the original in the pauses near it."""
    nf = n // FRAME
    p = np.empty(nf, dtype=np.float64)
    for i in range(0, nf, 6000):
        x = o[i * FRAME:min(nf, i + 6000) * FRAME].astype(np.float32) / 32768.0
        k = len(x) // FRAME
        p[i:i + k] = 10.0 * np.log10((x[:k * FRAME] ** 2).reshape(k, FRAME, 2).mean(axis=(1, 2)) + 1e-14)
    half = int(CROWD_REF_SEC * 100)
    centers = np.arange(0, nf + 100, 100)
    ref, prev = [], None
    for c in centers:
        a, b = max(0, c - half), min(nf, c + half)
        v = p[a:b][free[a:b]]
        if len(v) >= 50:
            v = v[v <= np.median(v) + 10.0]                                  # an event (a crash) does not set the level
            prev = float(10.0 * np.log10(np.mean(10.0 ** (v / 10.0))))

        ref.append(prev)
    first = next((r for r in ref if r is not None), -90.0)
    ref = np.array([first if r is None else r for r in ref])
    return np.interp(np.arange(nf), centers, ref), p


def _crowd_bed(mt, o, speech, free, n, out_path, part, info, log=None):
    """The place has a life of its own (see music_fill.texture_segs): nothing is made up for it. What the separator took away under the
    speech is put back from REAL pieces of the original's own pauses, held CROWD_BED_DB under the level the pauses have nearby, so the
    sound does not collapse every time somebody speaks. Only the missing part is added (what the separated background still holds
    under the speech counts)."""
    segs = _pool(o, free, n, CROWD_POOL_MAX_SEC)
    pieces = _Pieces(segs)
    if pieces.seconds() < 2 * CROWD_GRAIN_SEC or not pieces.power:
        info['reason'] = 'a place with life in it, and too little real sound in its pauses to put back under the speech'
        return info
    under = 1.0 - _weights(speech)
    nf = n // FRAME
    ref_db, _ = _pause_reference_db(o, free, n)
    target_pow = 10.0 ** ((ref_db + CROWD_BED_DB) / 10.0)
    frame_t = (np.arange(nf) + 0.5) * FRAME
    block = int(BLOCK_SEC * RATE)
    need_sec = 0.0
    added = []
    with wave.open(str(part), 'wb') as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        for a in range(0, n, block):
            c = min(n, a + block)
            x = mt[a:c].astype(np.float32) / 32768.0
            f0, f1 = a // FRAME, min(nf, (c + FRAME - 1) // FRAME)
            k = f1 - f0
            seg = x[:k * FRAME] if len(x) >= k * FRAME else np.pad(x, ((0, k * FRAME - len(x)), (0, 0)))
            m_pow = _smooth((seg ** 2).reshape(k, FRAME, 2).mean(axis=(1, 2)), 50)           # what the separated background still holds
            add = np.maximum(target_pow[f0:f1] - m_pow, 0.0) * under[f0:f1]
            s_f = np.sqrt(add / pieces.power)
            s = np.interp(np.arange(a, c), frame_t[f0:f1], s_f) if k > 1 else np.zeros(c - a)
            y = x.copy()
            live = s > 1e-4
            if live.any():
                edge = np.flatnonzero(np.diff(np.concatenate(([0], live.astype(np.int8), [0]))))
                for r0, r1 in zip(edge[0::2], edge[1::2]):
                    y[r0:r1] += pieces.take(int(r1 - r0)) * s[r0:r1, None].astype(np.float32)
                    need_sec += (r1 - r0) / RATE
                added.append(float(np.mean(s[live] ** 2)))
            out.writeframes(np.clip(np.rint(y * 32768.0), -32768, 32767).astype('<i2').tobytes())
    part.replace(out_path)
    info['ok'] = True
    info['crowd_bed'] = {'sec_laid': round(float(need_sec), 1), 'sec_real': round(float(pieces.seconds()), 1), 'repeats': round(float(need_sec / max(1.0, pieces.seconds())), 2)}
    info['added_db'] = round(float(10.0 * np.log10(np.mean(added) * pieces.power + 1e-20)), 1) if added else 0.0
    info['reason'] = (f"a place with life in it: real pieces of its own pauses ({pieces.seconds():.0f} s) are held {abs(CROWD_BED_DB):g} dB under the pause level "
                      f"while people speak ({need_sec:.0f} s laid, nothing made up)")
    return info


def bed_floor(matched, original, vocals, base, spans, out_path, floor_db=None, log=None):
    """Writes `out_path` = `matched` (the finished background) with the steady bed put back where the separator took it away under
    speech: per frequency bin the level is at least (pause level + floor_db). Only when the sound in the pauses of the original is
    steady and `original` is the same recording. Returns {"ok", "reason", "added_db"}; never raises."""
    floor_db = FLOOR_DB if floor_db is None else float(floor_db)
    info = {'ok': False, 'reason': '', 'added_db': 0.0}
    out_path = Path(out_path)
    srcs = [matched, original, vocals, base]
    raws = [out_path.with_name(out_path.name + f'.bf{i}.pcm') for i in range(4)]
    pcms = []
    part = out_path.with_name(out_path.name + '.part.wav')
    try:
        if not original or not Path(original).exists() or not vocals or not Path(vocals).exists() or not spans:
            info['reason'] = 'no original recording to take the steady sound from'
            return info
        for s, r in zip(srcs, raws):
            pcms.append(_open(s, r))
        if any(p is None for p in pcms):
            info['reason'] = 'an audio file is empty'
            return info
        mt, o, v, b = pcms
        n = min(len(mt), len(o), len(v), len(b))
        if any(abs(len(x) - len(mt)) > RATE // 4 for x in (o, v, b)):
            info['reason'] = 'the original recording has another length than the background'
            return info
        if not _same_recording(b, o, v, n):
            info['reason'] = 'the original recording does not match the separated sound'
            return info
        sinfo = {}
        speech = speech_frames(b, o, v, spans, n, sinfo)
        free = ~speech
        segs = _pool(o, free, n)
        if sum(len(s) for s in segs) / RATE < BED_MIN_POOL_SEC:
            info['reason'] = 'too little clean sound in the pauses to know the background'
            return info
        rms_db = 10.0 * np.log10(float(np.mean(np.concatenate(segs) ** 2)) + 1e-14)
        if rms_db < BED_MIN_DB:
            info['reason'] = 'the background in the pauses is silent'
            return info
        score = music_fill._steadiness_db(segs)
        if score is None or score > (DIRTY_STEADY_MAX_DB if sinfo.get('dirty') else music_fill.STEADY_MAX_DB):
            info['reason'] = 'the background is not steady, it is not rebuilt'
            return info
        tex = music_fill.texture_segs(segs)
        info['texture'] = tex
        if tex.get('ok') is False:
            # a crowd, a restaurant, a street: made-up noise with its average spectrum sounds like wind, so nothing is made up for it;
            # what is missing under the speech comes back from real pieces of the scene's own pauses
            return _crowd_bed(mt, o, speech, free, n, out_path, part, info, log)
        target = _pool_power(segs) * (10.0 ** (floor_db / 10.0))          # power per bin of one analysis frame
        a_ = np.concatenate([s[:, 0] for s in segs]); b_ = np.concatenate([s[:, 1] for s in segs])
        c_ = np.corrcoef(a_, b_)[0, 1] if len(a_) > 1 else 0.0
        rho = float(np.clip(c_ if np.isfinite(c_) else 0.0, 0.0, 0.98))
        w_free = _weights(speech)
        under = 1.0 - w_free                                                # 1 under speech, 0 in the pauses
        nf = n // FRAME
        frame_t = (np.arange(nf) + 0.5) * FRAME / RATE
        rng = np.random.default_rng(12345)
        block, ctx = int(BLOCK_SEC * RATE), int(CTX_SEC * RATE)
        win_gain = float(np.sum(np.hanning(N_FFT)) / 2.0)                   # scipy's stft scaling (sum of window / 2 for a one-sided spectrum)
        added = 0.0
        count = 0
        with wave.open(str(part), 'wb') as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(RATE)
            for a in range(0, n, block):
                c = min(n, a + block)
                lo, hi = max(0, a - ctx), min(n, c + ctx)
                x = mt[lo:hi].astype(np.float32) / 32768.0
                ys = []
                common = None
                for ch in range(2):
                    f, t, Z = stft(x[:, ch], RATE, window='hann', nperseg=N_FFT, noverlap=N_FFT - HOP, boundary='zeros', padded=True)
                    # power of one cell in the same units as the pool power (|rfft(frame * hann)|^2): scipy divides by sum(win)
                    scale = float(np.sum(np.hanning(N_FFT)))
                    P_cell = (np.abs(Z) * scale) ** 2
                    deficit = np.maximum(target[ch][:, None] - P_cell, 0.0)
                    tt = lo / RATE + t
                    wgt = np.interp(tt, frame_t, under)[None, :]
                    amp = np.sqrt(deficit * wgt) / scale * (10.0 ** (SYNTH_CAL_DB / 20.0))
                    noise = rng.standard_normal(Z.shape) + 1j * rng.standard_normal(Z.shape)
                    if ch == 0:
                        common = rng.standard_normal(Z.shape) + 1j * rng.standard_normal(Z.shape)
                    own = noise
                    mix = np.sqrt(rho) * common + np.sqrt(1.0 - rho) * own
                    Zn = Z + amp * mix / np.sqrt(2.0)
                    _, y = istft(Zn, RATE, window='hann', nperseg=N_FFT, noverlap=N_FFT - HOP, boundary=True)
                    y = y[:hi - lo]
                    if len(y) < hi - lo:
                        y = np.pad(y, (0, hi - lo - len(y)))
                    ys.append(y)
                    added += float(np.mean(amp ** 2)) / (10.0 ** (SYNTH_CAL_DB / 10.0))
                    count += 1
                y = np.stack(ys, axis=1)[a - lo:c - lo]
                out.writeframes(np.clip(np.rint(y * 32768.0), -32768, 32767).astype('<i2').tobytes())
        part.replace(out_path)
        info['ok'] = True
        info['added_db'] = round(10.0 * np.log10(added / max(1, count) + 1e-20), 1)
        info['reason'] = f'the steady background is held at least {abs(floor_db):g} dB under its pause level while people speak'
        return info
    except Exception as ex:
        info['reason'] = f'not used ({str(ex)[:160]})'
        if log:
            log('bed floor: ' + info['reason'])
        return info
    finally:
        _close(pcms)
        for p in raws + [part]:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
