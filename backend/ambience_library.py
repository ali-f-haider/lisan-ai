"""Licensed ambience selection and bounded, deterministic stereo looping.

Only catalogue entries approved for automatic use may be selected. Unknown
locations, silence and tonal references do not acquire a guessed atmosphere.
"""
import hashlib
import json
from pathlib import Path

import numpy as np
import soundfile as sf

RATE = 44100
BLOCK = 10 * RATE
ROOT = Path(__file__).resolve().parent / 'ambience_assets'
_verified = {}


def characteristics(chunks):
    """Broad spectrum, stereo width and level range; accept bounded float blocks."""
    powers, spectra = [], []
    sums = np.zeros(3, np.float64)
    peak, count = 0.0, 0
    for x in chunks:
        x = np.asarray(x, np.float32)
        if x.ndim != 2 or x.shape[1] != 2 or len(x) > BLOCK or not np.isfinite(x).all():
            raise ValueError('Invalid ambience audio block')
        if not len(x):
            continue
        peak = max(peak, float(np.max(np.abs(x))))
        sums += (np.sum(x[:, 0] ** 2, dtype=np.float64), np.sum(x[:, 1] ** 2, dtype=np.float64),
                 np.sum(x[:, 0] * x[:, 1], dtype=np.float64))
        count += len(x)
        for a in range(0, len(x), RATE):
            y = x[a:a + RATE]
            powers.append(float(np.mean(y ** 2)))
        k = 2048
        y = x[:len(x) // k * k]
        if len(y):
            p = (np.abs(np.fft.rfft(y.reshape(-1, k, 2) * np.hanning(k)[None, :, None], axis=1)) ** 2).mean(axis=(0, 2))
            f = np.fft.rfftfreq(k, 1 / RATE)
            spectra.append([float(p[(f >= a) & (f < b)].sum()) for a, b in
                            zip((40, 150, 400, 1000, 3000), (150, 400, 1000, 3000, 10000))])
    if not count or not spectra:
        raise ValueError('Ambience reference is too short')
    bands = np.mean(spectra, axis=0)
    bands /= max(float(bands.sum()), 1e-20)
    levels = 10 * np.log10(np.asarray(powers) + 1e-14)
    return {'rms_db': round(float(10 * np.log10((sums[0] + sums[1]) / (2 * count) + 1e-14)), 3),
            'peak': round(peak, 6), 'bands': [round(float(v), 6) for v in bands],
            'level_range_db': round(float(np.percentile(levels, 90) - np.percentile(levels, 10)), 3),
            'stereo_correlation': round(float(np.clip(sums[2] / np.sqrt(max(sums[0] * sums[1], 1e-20)), -1, 1)), 4)}


def inspect(path):
    with sf.SoundFile(path) as audio:
        if audio.samplerate != RATE or audio.channels != 2 or not 8 <= len(audio) / RATE <= 600:
            raise ValueError('Ambience must be stereo 44.1 kHz and 8 to 600 seconds long')
        features = characteristics(audio.blocks(blocksize=BLOCK, dtype='float32', always_2d=True))
        return {'duration_sec': round(len(audio) / RATE, 6), 'sample_rate': RATE, 'channels': 2, 'characteristics': features}


def catalogue(root=None):
    root = Path(root or ROOT).resolve()
    try:
        path = root / 'catalogue.json'
        if path.stat().st_size > 256000:
            return []
        data = json.loads(path.read_text(encoding='utf-8'))
        return [v for v in data['assets'] if isinstance(v, dict)] if data.get('schema') == 1 else []
    except (OSError, ValueError, KeyError, TypeError):
        return []


def asset_path(asset, root=None):
    root = Path(root or ROOT).resolve()
    path = (root / str(asset['file'])).resolve()
    if path.parent != root or asset.get('license') != 'CC0-1.0':
        raise ValueError('Unapproved ambience source')
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size, asset.get('sha256'))
    if key not in _verified:
        with path.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        if digest != asset.get('sha256'):
            raise ValueError('Ambience checksum failed')
        with sf.SoundFile(path) as audio:
            if audio.channels != 2 or audio.samplerate != RATE or not 8 <= len(audio) / RATE <= 600:
                raise ValueError('Invalid ambience format or length')
        if len(_verified) > 64:
            _verified.clear()
        _verified[key] = True
    return path


def select(reference, scene, root=None):
    """A confident location AND a clean audible reference are required, not either alone."""
    if not scene or scene.get('conf') != 'high' or scene.get('key') in ('unknown', 'vehicle', 'huge_space'):
        return None
    if reference['rms_db'] < -75 or reference.get('level_range_db', 0) > 12:
        return None
    candidates = []
    for asset in catalogue(root):
        if not asset.get('auto_approved') or scene.get('key') not in asset.get('environments', []):
            continue
        # Outdoors requires a specific environment cue, not merely an outdoor frame.
        if scene.get('key') == 'outdoors' and not any(t in str(scene.get('place', '')).lower() for t in asset.get('tags', [])):
            continue
        try:
            features = asset['characteristics']
            a, b = np.asarray(reference['bands']), np.asarray(features['bands'])
            if a.shape != (5,) or b.shape != (5,) or not np.isfinite(a).all() or not np.isfinite(b).all():
                continue
            # Hellinger distance: level-independent, deliberately conservative.
            distance = float(np.sqrt(np.sum((np.sqrt(np.maximum(a, 0)) - np.sqrt(np.maximum(b, 0))) ** 2) / 2))
            width = abs(reference['stereo_correlation'] - features['stereo_correlation'])
            if distance > 0.45 or width > 0.8:
                continue
            asset_path(asset, root)
            candidates.append((distance + 0.12 * width, asset['id'], asset))
        except (KeyError, TypeError, ValueError, OSError, RuntimeError):
            continue
    return min(candidates, key=lambda c: (c[0], c[1]))[2] if candidates else None


class LoopReader:
    """Continuous long recording; compensate correlated overlap to avoid level swells.

    Memory holds only a <=2 s seam plus the requested <=10 s output block.
    Start phase is stable across exports and independent of block sizes.
    """
    def __init__(self, path, seed='', fade_sec=1.5):
        self.audio = sf.SoundFile(path)
        try:
            if self.audio.channels != 2 or self.audio.samplerate != RATE or not 8 <= len(self.audio) / RATE <= 600:
                raise ValueError('Invalid ambience loop')
            self.fade = max(1, min(int(min(2, max(0.05, fade_sec)) * RATE), len(self.audio) // 4))
            self.period = len(self.audio) - self.fade
            head = self.audio.read(self.fade, dtype='float32', always_2d=True)
            self.audio.seek(len(self.audio) - self.fade)
            tail = self.audio.read(self.fade, dtype='float32', always_2d=True)
            rho = float(np.clip(np.sum(head * tail, dtype=np.float64) / np.sqrt(max(float(np.sum(head ** 2, dtype=np.float64) * np.sum(tail ** 2, dtype=np.float64)), 1e-20)), -0.95, 0.95))
            t = np.linspace(0, np.pi / 2, self.fade, dtype=np.float32)[:, None]
            c, s = np.cos(t), np.sin(t)
            self.seam = (tail * c + head * s) / np.sqrt(c * c + s * s + 2 * rho * c * s)
            self.position = int.from_bytes(hashlib.sha256(str(seed).encode()).digest()[:8], 'big') % self.period
        except Exception:
            self.audio.close()
            raise

    def take(self, count):
        if not 0 <= count <= BLOCK:
            raise ValueError('Ambience reads are limited to ten seconds')
        out = np.empty((count, 2), np.float32)
        done = 0
        while done < count:
            pos = self.position
            if pos < self.fade:
                take = min(count - done, self.fade - pos)
                out[done:done + take] = self.seam[pos:pos + take]
            else:
                take = min(count - done, self.period - pos)
                self.audio.seek(pos)
                out[done:done + take] = self.audio.read(take, dtype='float32', always_2d=True)
            done += take
            self.position = (pos + take) % self.period
        if not np.isfinite(out).all():
            raise ValueError('Invalid ambience samples')
        return out

    def close(self):
        self.audio.close()
