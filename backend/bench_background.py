"""Scores the background engine (dub_background.prepare) on mixtures whose true parts are known.

Data: the Divide and Remaster (DnR v3) test set, or any folder with the same layout. For every mixture id <id>:
    wav/<id>.wav                     the mixture (stereo, 44.1 kHz)
    sep/htdemucs/<id>/{vocals,no_vocals}.wav   the voice separation of the mixture
    truth/<id>_{speech,music,sfx,sfx_bg,sfx_fg}.wav   the true parts (stereo, 44.1 kHz)
    asr.json                         {id: {"words": [[start, end], ...], "lines": [[start, end], ...]}}  (from the clean speech)

Run (from backend):   python bench_background.py --root /path/to/dnr --out results.json [--ids 000000,000001]
It compares, per mixture, what the separator alone gives ("raw") with what the engine delivers ("engine") against the TRUE
background (music + sound effects):
    pause_db / speech_db   level difference to the true background in the pauses / under the speech (0 is perfect)
    pause_sdr / speech_sdr how close the waveform is to the true background there (dB, higher is better; 99 = identical)
    dropout / excess       share of 0.5 s windows more than 10 dB under / 6 dB over the true background (lower is better)
    leak_db                how much of the ORIGINAL voice is left in it (amplitude, dB; -30 or lower is inaudible)
    fg_db                  level at the loudest foreground effects (plates, impacts) against the truth (0 is perfect)
    hiss_db                level of the 2.5-10 kHz band under the speech against the truth (0 is perfect; +3 and more is audible hiss:
                           what a separator leaves of a voice -- breath, "s", "sh" -- sounds like wind)
Nothing is charged: the music model is switched off, so only the free path and the fallbacks are scored."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dub_background
import music_fill

SR = 44100
PAD = 0.25
WIN = SR // 2


def mono(path):
    x, sr = sf.read(str(path), dtype='float32')
    assert sr == SR, (path, sr)
    return x.mean(axis=1) if x.ndim > 1 else x


def db(x):
    return 10.0 * np.log10(float(np.mean(np.square(x, dtype=np.float64))) + 1e-14)


def frames_mask(spans, n):
    nf = n // 441
    m = np.zeros(nf, dtype=bool)
    for a, b in spans:
        m[max(0, int((a - PAD) * 100)):min(nf, int((b + PAD) * 100) + 1)] = True
    full = np.repeat(m, 441)
    return np.pad(full, (0, max(0, n - len(full))))[:n]


def sdr(x, t, mask):
    if mask.sum() < SR // 2:
        return None
    err = np.mean((x[mask] - t[mask]) ** 2) + 1e-14
    return float(min(99.0, 10 * np.log10(np.mean(t[mask] ** 2) / err + 1e-14)))


def score(x, bg, speech, fg, speech_mask):
    n = min(len(x), len(bg), len(speech), len(fg))
    x, bg, speech, fg, sm = x[:n], bg[:n], speech[:n], fg[:n], speech_mask[:n]
    out = {}
    out['pause_db'] = round(db(x[~sm]) - db(bg[~sm]), 2) if (~sm).sum() > SR else None
    out['speech_db'] = round(db(x[sm]) - db(bg[sm]), 2) if sm.sum() > SR else None
    out['pause_sdr'] = sdr(x, bg, ~sm)
    out['speech_sdr'] = sdr(x, bg, sm)
    k = n // WIN
    ex = np.square(x[:k * WIN]).reshape(k, WIN).mean(axis=1)
    et = np.square(bg[:k * WIN]).reshape(k, WIN).mean(axis=1)
    loud = 10 * np.log10(et + 1e-14) > -70
    d = 10 * np.log10(ex + 1e-14) - 10 * np.log10(et + 1e-14)
    out['dropout'] = round(float(np.mean(d[loud] < -10)), 3) if loud.any() else None
    out['excess'] = round(float(np.mean(d[loud] > 6)), 3) if loud.any() else None
    sel = sm
    if sel.sum() > SR:
        A = np.stack([bg[sel], speech[sel]], axis=1).astype(np.float64)
        coef, *_ = np.linalg.lstsq(A, x[sel].astype(np.float64), rcond=None)
        out['leak_db'] = round(float(20 * np.log10(abs(coef[1]) + 1e-6)), 1)
    else:
        out['leak_db'] = None
    a, b = band_db(x, sm, 2500, 10000), band_db(bg, sm, 2500, 10000)
    out['hiss_db'] = round(float(a - b), 2) if a is not None and b is not None else None
    f = n // 441
    ef = np.square(fg[:f * 441]).reshape(f, 441).mean(axis=1)
    if f > 100 and ef.max() > 1e-9:
        top = ef >= np.percentile(ef, 95)
        xs = np.square(x[:f * 441]).reshape(f, 441).mean(axis=1)
        ts = np.square(bg[:f * 441]).reshape(f, 441).mean(axis=1)
        out['fg_db'] = round(float(10 * np.log10(xs[top].mean() + 1e-14) - 10 * np.log10(ts[top].mean() + 1e-14)), 2)
    else:
        out['fg_db'] = None
    return out


def band_db(x, mask, lo, hi):
    n = min(len(x), len(mask))
    frames = np.flatnonzero(np.repeat(mask[::441][:n // 441], 1))
    if len(frames) < 100:
        return None
    seg = np.concatenate([x[i * 441:(i + 1) * 441] for i in frames[:6000]])
    f = np.fft.rfftfreq(2048, 1 / SR)
    k = len(seg) // 2048
    P = np.abs(np.fft.rfft(seg[:k * 2048].reshape(k, 2048) * np.hanning(2048), axis=1)) ** 2
    return 10 * np.log10(P[:, (f >= lo) & (f < hi)].mean(axis=0).sum() + 1e-14)


def synthetic_dub(lines, n, path):
    rng = np.random.default_rng(1)
    dub = np.zeros(n, np.float32)
    for a, b in lines:
        i, j = int(a * SR), min(n, int(b * SR))
        if j > i:
            t = np.arange(j - i) / SR
            dub[i:j] = (rng.standard_normal(j - i) * 0.05 * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))).astype(np.float32)
    sf.write(str(path), dub, SR)


def run_one(root, mid, asr, drop=0.0):
    sep = root / 'sep' / 'htdemucs' / mid
    tr = root / 'truth'
    spans = [tuple(map(float, s)) for s in asr['words']] + [tuple(map(float, s)) for s in asr['lines']]
    music, sfx, speech, fg = (mono(tr / f'{mid}_{k}.wav') for k in ('music', 'sfx', 'speech', 'sfx_fg'))
    n = min(len(music), len(sfx), len(speech), len(fg))
    bg = music[:n] + sfx[:n]
    sm = frames_mask(spans, n)
    if drop > 0:      # the transcript missed some lines: the engine is told about fewer words than were spoken
        rng = np.random.default_rng(7)
        keep = [i for i in range(len(asr['lines'])) if rng.random() >= drop]
        kept = [tuple(map(float, asr['lines'][i])) for i in keep]
        spans = [w for w in spans[:len(asr['words'])] if any(a - 0.05 <= w[0] <= b + 0.05 for a, b in kept)] + kept
    raw = mono(sep / 'no_vocals.wav')
    res = {'raw': score(raw, bg, speech, fg, sm)}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        synthetic_dub(asr['lines'], n, tmp / 'dub.wav')
        music_fill.ENABLED = False
        r = dub_background.prepare(str(sep / 'no_vocals.wav'), str(sep / 'vocals.wav'), str(tmp / 'dub.wav'), tmp, 't', spans,
                                   key='x', preserve_music=True, strict=False, original=str(root / 'wav' / f'{mid}.wav'))
        res['engine'] = score(mono(r['path']), bg, speech, fg, sm)
        res['notes'] = {'pauses': r['music_fill'].get('original_in_pauses'), 'bed': r['music_fill'].get('steady_bed'),
                        'unavailable': r['music_fill'].get('unavailable')}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--out', default='bench_results.json')
    ap.add_argument('--ids', default='')
    ap.add_argument('--drop', type=float, default=0.0, help='share of lines the transcript missed (tests the safety against missed speech)')
    ap.add_argument('--gap', type=float, default=None, help='override pause_bed.DIRTY_GAP_DB')
    ap.add_argument('--share', type=float, default=None, help='override pause_bed.DIRTY_SHARE')
    a = ap.parse_args()
    root = Path(a.root)
    import pause_bed
    if a.gap is not None:
        pause_bed.DIRTY_GAP_DB = a.gap
    if a.share is not None:
        pause_bed.DIRTY_SHARE = a.share
    asr = json.loads((root / 'asr.json').read_text(encoding='utf-8'))
    ids = [i for i in a.ids.split(',') if i] or sorted(asr)
    results = {}
    for mid in ids:
        if not (root / 'sep' / 'htdemucs' / mid / 'vocals.wav').exists():
            continue
        try:
            results[mid] = run_one(root, mid, asr[mid], a.drop)
        except Exception as ex:
            results[mid] = {'error': str(ex)[:200]}
        r = results[mid]
        if 'engine' in r:
            e, w = r['engine'], r['raw']
            print(f"{mid} raw: pause {w['pause_db']} speech {w['speech_db']} drop {w['dropout']} leak {w['leak_db']} fg {w['fg_db']} hiss {w['hiss_db']} | "
                  f"engine: pause {e['pause_db']} speech {e['speech_db']} drop {e['dropout']} exc {e['excess']} leak {e['leak_db']} fg {e['fg_db']} hiss {e['hiss_db']}", flush=True)
        else:
            print(mid, r, flush=True)
    Path(a.out).write_text(json.dumps(results, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
