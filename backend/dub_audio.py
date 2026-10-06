"""Local background loudness matching. Work in bounded PCM blocks, including hour-long media."""
from pathlib import Path
import os
import subprocess
import wave
import numpy as np

RATE, CHANNELS, HOP = 44100, 2, 22050


def _decode(path, raw):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(path), '-vn', '-ac', '2',
                    '-ar', str(RATE), '-f', 's16le', str(raw)], check=True, timeout=1800,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    n = Path(raw).stat().st_size // 4
    return np.memmap(raw, dtype='<i2', mode='r', shape=(n, 2))


def levels(pcm):
    result = np.empty((len(pcm) + HOP - 1) // HOP)
    for i in range(len(result)):
        part = pcm[i * HOP:(i + 1) * HOP].astype(np.float32) / 32768
        result[i] = float(np.sqrt(np.mean(part * part))) if len(part) else 0
    return result


def profile(path, raw):
    pcm = None
    try:
        pcm = _decode(path, raw)
        return levels(pcm).round(7).tolist()
    finally:
        if pcm is not None:
            pcm._mmap.close()
        Path(raw).unlink(missing_ok=True)


def gain_plan(original_bg, repaired_bg, original_voice=None, dubbed_voice=None, spans=(), lower=()):
    n = len(repaired_bg)
    reference = np.zeros(n)
    reference[:min(n, len(original_bg))] = original_bg[:n]
    target = reference.copy()
    measurements = []
    if original_voice is not None and dubbed_voice is not None:
        for a, b in spans:
            i, j = max(0, int(a * 2)), min(n, int(np.ceil(b * 2)))
            if j <= i:
                continue
            vo = float(np.sqrt(np.mean(original_voice[i:j] ** 2))) if len(original_voice[i:j]) else 0
            vd = float(np.sqrt(np.mean(dubbed_voice[i:j] ** 2))) if len(dubbed_voice[i:j]) else 0
            bg = float(np.sqrt(np.mean(reference[i:j] ** 2)))
            if vo > 1e-4 and vd > 1e-4:
                target[i:j] = reference[i:j] * np.clip(vd / vo, 0.25, 4)
            measurements.append({'start': a, 'end': b, 'original_voice_rms': vo,
                                 'original_background_rms': bg, 'dubbed_voice_rms': vd})
    if lower:       # (start s, end s, dB): the background is wanted this much lower there (a later entry replaces an earlier one)
        adjust = np.zeros(n)
        for a, b, db in lower:
            i, j = max(0, int(a * 2)), min(n, int(np.ceil(b * 2)))
            if j > i:
                adjust[i:j] = float(db)
        target = target * 10.0 ** (adjust / 20.0)
    gain = np.ones(n)
    usable = repaired_bg > 1e-6
    gain[usable] = np.clip(target[usable] / repaired_bg[usable], 0.25, 4)
    gain[usable & (target < 1e-6)] = 0
    return gain, measurements


def match_background(original_bg, repaired_bg, out_path, original_voice=None, dubbed_voice=None, spans=(), lower=()):
    paths, maps = [], []
    out_path = Path(out_path)
    tmp = out_path.with_name(out_path.name + '.part.wav')
    try:
        powers = []
        for i, source in enumerate((original_bg, repaired_bg, original_voice, dubbed_voice)):
            if source is None or not Path(source).exists():
                powers.append(None)
                continue
            raw = out_path.with_name(out_path.name + f'.level{i}.pcm')
            paths.append(raw)
            pcm = _decode(source, raw)
            maps.append(pcm)
            powers.append(levels(pcm))
        if powers[0] is None or powers[1] is None:
            raise ValueError('Background references are missing')
        gains, measurements = gain_plan(*powers, spans=spans, lower=lower)
        source = maps[1]
        centers = (np.arange(len(gains)) + 0.5) * HOP
        with wave.open(str(tmp), 'wb') as out:
            out.setnchannels(2); out.setsampwidth(2); out.setframerate(RATE)
            for i in range(0, len(source), RATE * 5):
                j = min(len(source), i + RATE * 5)
                gain = np.interp(np.arange(i, j), centers, gains)
                samples = source[i:j].astype(np.float32) * gain[:, None]
                # A local peak guard only attenuates; no limiter automatic make-up gain.
                peak = float(np.max(np.abs(samples)))
                if peak > 31784:
                    samples *= 31784 / peak
                out.writeframes(np.clip(np.rint(samples), -32768, 32767).astype('<i2').tobytes())
        os.replace(tmp, out_path)
        return {'segments': measurements, 'min_gain': float(gains.min()), 'max_gain': float(gains.max())}
    finally:
        for pcm in maps:
            pcm._mmap.close()
        for path in paths + [tmp]:
            path.unlink(missing_ok=True)
