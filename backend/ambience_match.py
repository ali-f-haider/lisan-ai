"""Free, scene-scoped ambience fill from clean parts of the original recording.

No model calls; samples come only from the existing speech-free mask. A short
reference measures a bed but is never repeated on its own.
Audio is rendered in blocks.
"""
import json
import re
import wave
from contextlib import ExitStack
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d

import music_fill

RATE = music_fill.RATE
FRAME = 441
MIN_REFERENCE_SEC = 0.4
POOL_SEC = 12.0
GRAIN_SEC = 1.5
FADE_SEC = 0.15
BLOCK_SEC = 10.0
EVENT_DB = 8.0
SHAPE_CHANGE_DB = 6.0
MAX_UNSCOPED_DISTANCE_SEC = 90.0
_JOB = re.compile(r"[A-Za-z0-9_-]{20,100}")


def cached_scenes(work, tag, background):
    """Read this job's existing scene cache only; never search other jobs or call AI."""
    candidates = []
    if str(tag).startswith('merge_'):
        candidates.append((Path(work), str(tag)[6:]))
    elif str(tag).endswith('_price'):
        candidates.append((Path(work), str(tag)[:-6]))
    parent = Path(background).parent
    candidates.append((parent, parent.name))
    for directory, job_id in candidates:
        if not _JOB.fullmatch(job_id):
            continue
        path = directory / f'{job_id}_scenes.json'
        try:
            if path.stat().st_size > 512000:
                continue
            scenes = json.loads(path.read_text(encoding='utf-8')).get('scenes')
            if isinstance(scenes, list):
                return scenes
        except (OSError, ValueError, AttributeError):
            pass
    return None


def _runs(mask):
    edge = np.flatnonzero(np.diff(np.concatenate(([0], np.asarray(mask, dtype=np.int8), [0]))))
    return [(int(a), int(b)) for a, b in zip(edge[::2], edge[1::2])]


def _power_frames(pcm, nf):
    levels = np.empty(nf, np.float32)
    for a in range(0, nf, 1000):
        b = min(nf, a + 1000)
        x = pcm[a * FRAME:b * FRAME].astype(np.float32) / 32768.0
        levels[a:b] = (x ** 2).reshape(b - a, FRAME, 2).mean(axis=(1, 2))
    return levels


def _write_original(out, pcm, a, b):
    for c in range(a, b, int(BLOCK_SEC * RATE)):
        out.writeframes(np.asarray(pcm[c:min(b, c + int(BLOCK_SEC * RATE))], dtype='<i2').tobytes())


def _shape(x):
    k = 2048
    x = x[:len(x) // k * k]
    if not len(x):
        return None
    z = np.fft.rfft(x.reshape(-1, k, 2) * np.hanning(k)[None, :, None], axis=1)
    p = (np.abs(z) ** 2).mean(axis=(0, 2))
    f = np.fft.rfftfreq(k, 1 / RATE)
    bands = np.array([10 * np.log10(p[(f >= a) & (f < b)].mean() + 1e-14)
                      for a, b in zip((100, 300, 800, 2000, 5000), (300, 800, 2000, 5000, 10000))])
    return bands - bands.mean()


def _tonal(x):
    """Do not loop melodies or voiced words: reject persistent dominant narrow peaks."""
    k = 2048
    x = x[:len(x) // k * k]
    if not len(x):
        return True
    z = np.fft.rfft(x.reshape(-1, k, 2) * np.hanning(k)[None, :, None], axis=1)
    f = np.fft.rfftfreq(k, 1 / RATE)
    p = (np.abs(z[:, (f >= 100) & (f < 6000)]) ** 2).sum(axis=2)
    dominance = p.max(axis=1) / (p.sum(axis=1) + 1e-14)
    return bool(np.mean(dominance > 0.25) > 0.2)


def regions(scenes, free, original):
    """Explicit scenes never share donors. Without them, use acoustic changes and proximity."""
    nf = len(free)
    supplied = []
    for scene in scenes or []:
        if not isinstance(scene, dict):
            continue
        try:
            a, b = float(scene['t0']), float(scene['t1'])
            if not (np.isfinite(a) and np.isfinite(b) and b > a):
                continue
            a, b = max(0, min(nf, int(a * 100))), max(0, min(nf, int(b * 100)))
            if b > a:
                supplied.append((a, b))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    if supplied:
        out, pos = [], 0
        for a, b in sorted(supplied):
            a = max(pos, a)
            if a > pos:
                out.append((pos, a))       # unlabelled gaps are separate, never assigned to a neighbour
            if b > a:
                out.append((a, b))
                pos = b
        if pos < nf:
            out.append((pos, nf))
        acoustic, _ = regions(None, free, original)
        cuts = [b for _, b in acoustic[:-1]]
        return [(c, d) for a, b in out for c, d in zip([a] + [v for v in cuts if a < v < b],
                                                        [v for v in cuts if a < v < b] + [b])], True
    cuts, previous = [0], None
    for a, b in _runs(free):
        for c in range(a, b, 150):
            d = min(b, c + 150)
            if d - c < 40:
                continue
            x = original[c * FRAME:d * FRAME].astype(np.float32) / 32768.0
            shape = _shape(x)
            if shape is None or float(np.mean(x ** 2)) < 1e-8:
                continue
            if previous is not None and float(np.mean(np.abs(shape - previous[2]))) > SHAPE_CHANGE_DB:
                cut = c
                if cut > cuts[-1]:
                    cuts.append(cut)
            # A music onset can change tonality without changing broad spectral shape.
            tonal = _tonal(x)
            if previous is not None and tonal != previous[3]:
                cut = c
                if cut > cuts[-1]:
                    cuts.append(cut)
            previous = (c, d, shape, tonal)
    cuts.append(nf)
    return [(a, b) for a, b in zip(cuts[:-1], cuts[1:]) if b > a], False


def _references(original, free, power, a, b):
    """Exclude events; cap copied audio before conversion, including very long pauses."""
    audible = free[a:b] & (power[a:b] > 1e-8)
    if not audible.any():
        return [], [], []
    db = 10 * np.log10(power[a:b] + 1e-14)
    # A sustained increase in the room's level is not an impact. Compare with
    # its local temporal median so those quieter/louder sections stay usable.
    from scipy.ndimage import median_filter
    typical = median_filter(db, size=101, mode='nearest')
    event = db > typical + EVENT_DB
    # An impact and its ringing tail must not become a repeating donor.
    from scipy.ndimage import maximum_filter1d
    event = maximum_filter1d(event, size=21, mode='nearest')
    safe = audible & ~event
    candidates = []
    for c, d in _runs(safe):
        c, d = a + c + 3, a + d - 3
        if d - c >= int(MIN_REFERENCE_SEC * 100):
            candidates.append((c, d))
    # Spread the finite pool over the scene instead of taking only its longest/first pause.
    portions = []
    for c, d in candidates:
        for start in range(c, d, int(GRAIN_SEC * 100)):
            end = min(d, start + int(GRAIN_SEC * 100))
            if end - start >= int(MIN_REFERENCE_SEC * 100):
                portions.append((start, end))
    accepted, times, levels = [], [], []
    for c, d in portions:
        count = min((d - c) * FRAME, int(GRAIN_SEC * RATE))
        if count < int(MIN_REFERENCE_SEC * RATE):
            continue
        x = original[c * FRAME:c * FRAME + count].astype(np.float32) / 32768.0
        if _tonal(x):
            continue
        accepted.append((c * FRAME, count))
        times.append((c + count / FRAME / 2) / 100)
        levels.append(float(10 * np.log10(np.mean(x ** 2) + 1e-14)))
    chunks = []
    if accepted:
        # Retain measurements for ALL references, but copy a small, evenly spread pool.
        keep = min(len(accepted), max(1, int(POOL_SEC / GRAIN_SEC)))
        for index in np.unique(np.linspace(0, len(accepted) - 1, keep, dtype=int)):
            c, count = accepted[int(index)]
            chunks.append(original[c:c + count].astype(np.float32) / 32768.0)
    return chunks, times, levels


class Pieces:
    """A bounded stream of real stereo samples, with overlap retained between reads."""
    def __init__(self, chunks, seed=7):
        self.rng = np.random.default_rng(seed)
        powers = [float(np.mean(x ** 2)) for x in chunks]
        self.power = float(np.median(powers))
        self.grains = [x * np.float32(np.sqrt(self.power / max(p, 1e-14))) for x, p in zip(chunks, powers)]
        self.fade = min(int(FADE_SEC * RATE), min(len(g) for g in self.grains) // 4)
        self.order, self.last = [], -1
        self.pending = np.zeros((0, 2), np.float32)
        self.tail = None

    def _next(self):
        if not self.order:
            self.order = [int(i) for i in self.rng.permutation(len(self.grains))]
            if len(self.order) > 1 and self.order[0] == self.last:
                self.order.append(self.order.pop(0))
        self.last = self.order.pop(0)
        return self.grains[self.last]

    def take(self, count):
        while len(self.pending) < count:
            g = self._next()
            f = self.fade
            if self.tail is None:
                add = g[:-f]
            else:
                t = np.linspace(0, np.pi / 2, f, dtype=np.float32)[:, None]
                c, s = np.cos(t), np.sin(t)
                head = g[:f]
                rho = float(np.clip(np.sum(self.tail * head, dtype=np.float64) /
                                     np.sqrt(max(float(np.sum(self.tail ** 2, dtype=np.float64) * np.sum(head ** 2, dtype=np.float64)), 1e-20)), -0.95, 0.95))
                join = (self.tail * c + head * s) / np.sqrt(c * c + s * s + 2 * rho * c * s)
                add = np.concatenate((join, g[f:-f]))
            self.tail = g[-f:]
            self.pending = np.concatenate((self.pending, add))
        out = self.pending[:count].copy()
        self.pending = self.pending[count:].copy()
        return out


def restore(matched, original, speech, free, n, out_path, part, scenes=None, level_db=-3.0):
    """Add only missing ambience, leaving original pauses and surviving effects intact.

The steady single-room path remains in pause_bed. Mixed scenes and changing
textures use real samples here, so a global steadiness test cannot reject them.
"""
    nf = len(free)
    ranges, explicit = regions(scenes, free, original)
    power = _power_frames(original, nf)
    held_power = _power_frames(matched, nf)
    info = {'ok': False, 'reason': '', 'added_db': 0.0, 'scoped': len(ranges) > 1,
            'ambience_scenes': [], 'crowd_bed': {'sec_laid': 0.0, 'sec_real': 0.0, 'repeats': 0.0}}
    laid, real, added_power, added_count = 0.0, 0.0, 0.0, 0
    with ExitStack() as stack:
        out = None
        for a, b in ranges:
            chunks, times, levels = _references(original, free, power, a, b)
            record = {'start': a / 100, 'end': b / 100, 'reference_sec': round(sum(len(x) for x in chunks) / RATE, 2),
                      'filled_sec': 0.0, 'status': 'no clean nonmusical reference', 'boundary_method': 'scene cache and clean-pause acoustic changes'}
            sampler = None
            if chunks:
                texture = music_fill.texture_segs(chunks)
                steady = music_fill._steadiness_db(chunks)
                # Preserve the existing spectral floor for one steady room, including hums.
                level_range = float(np.percentile(levels, 90) - np.percentile(levels, 10))
                if (record['reference_sec'] >= 1.2 and len(ranges) == 1 and steady is not None and steady <= music_fill.STEADY_MAX_DB
                        and texture['ok'] is not False and level_range <= 3.0):
                    record['status'] = 'steady sound uses the existing spectral floor'
                elif record['reference_sec'] >= 3.0 and sum(len(x) >= RATE for x in chunks) >= 2:
                    sampler = Pieces(chunks)
                    record['status'] = 'real ambience samples'
                    real += record['reference_sec']
                else:
                    # A tiny original sample is measurement evidence, not a repeating bed.
                    import ambience_library as library
                    scene = next((s for s in scenes or [] if isinstance(s, dict)
                                  and float(s.get('t0', -1)) <= a / 100
                                  and float(s.get('t1', -1)) >= b / 100), None)
                    selected = library.select(library.characteristics(chunks), scene)
                    if selected:
                        sampler = library.LoopReader(library.asset_path(selected), seed=f"{selected['id']}:{a}:{b}")
                        stack.callback(sampler.close)
                        sampler.power = 10 ** (selected['characteristics']['rms_db'] / 10)
                        record['status'] = 'matched library ambience'
                        record['asset_id'] = selected['id']
                        record['replacement'] = True
                        record['loop_sec'] = round(sampler.period / RATE, 3)
                    else:
                        record['status'] = 'check background: insufficient clean ambience'
                        info['scoped'] = True  # do not fall through to another short-grain loop
            record['review'] = bool(np.any(speech[a:b] & (held_power[a:b] < 1e-8))) and (record['status'].startswith('check') or not chunks)
            info['ambience_scenes'].append(record)
            local_nf = b - a
            # Frame level follows the nearest measured clean reference, never the original speech.
            centers = (np.arange(a, b) + 0.5) / 100
            if sampler is not None:
                order = np.argsort(times)
                times_sorted = np.asarray(times)[order]
                ref = np.interp(centers, times_sorted, np.asarray(levels)[order])
                allowed = np.ones(local_nf, bool)
                if not explicit:
                    index = np.searchsorted(times_sorted, centers)
                    left = times_sorted[np.maximum(0, index - 1)]
                    right = times_sorted[np.minimum(len(times_sorted) - 1, index)]
                    distance = np.minimum(np.abs(centers - left), np.abs(centers - right))
                    allowed = distance <= MAX_UNSCOPED_DISTANCE_SEC
                target = 10 ** ((ref + float(level_db)) / 10)
                under = speech[a:b].astype(np.float32)
                # Smooth only inside the speech mask: clean pauses are bit-identical.
                under = np.minimum(under, uniform_filter1d(under, size=15, mode='constant'))
                deficit = np.maximum(target - held_power[a:b], 0) * under * allowed
                gains = uniform_filter1d(np.sqrt(deficit / sampler.power), size=15, mode='nearest')
                gains *= speech[a:b] & (deficit > 0)
                if not np.any(gains > 1e-4):
                    sampler = None
                    if not record.get('replacement'):
                        real -= record['reference_sec']
                    record['status'] = 'existing background is already sufficient'
            if out is None and sampler is not None:
                out = stack.enter_context(wave.open(str(part), 'wb'))
                out.setnchannels(2)
                out.setsampwidth(2)
                out.setframerate(RATE)
                _write_original(out, matched, 0, a * FRAME)
            if out is None:
                continue
            if sampler is None:
                _write_original(out, matched, a * FRAME, b * FRAME)
                continue
            step = int(BLOCK_SEC * 100)
            for c in range(a, b, step):
                d = min(b, c + step)
                x = matched[c * FRAME:d * FRAME].astype(np.float32) / 32768.0
                if sampler is not None:
                    # Measure surviving sound per frame: a loud effect never gets overwritten or turned down.
                    gain = gains[c - a:d - a]
                    positions = np.arange(len(x)) / FRAME
                    scale = np.interp(positions, np.arange(d - c), gain)
                    scale *= np.repeat(speech[c:d], FRAME)
                    bed = sampler.take(len(x)) * scale[:, None].astype(np.float32)
                    x += bed
                    seconds = float(np.count_nonzero(gain > 1e-4)) / 100
                    laid += seconds
                    record['filled_sec'] += seconds
                    added_power += float(np.sum(bed ** 2, dtype=np.float64))
                    added_count += len(bed) * 2
                out.writeframes(np.clip(np.rint(x * 32768), -32768, 32767).astype('<i2').tobytes())
            record['filled_sec'] = round(record['filled_sec'], 2)
        # Keep the fractional last frame too: never change the file's duration.
        if out is not None:
            out.writeframes(np.asarray(matched[nf * FRAME:n], dtype='<i2').tobytes())
    if laid:
        Path(part).replace(out_path)
        info['ok'] = True
        info['crowd_bed'] = {'sec_laid': round(laid, 2), 'sec_real': round(real, 2), 'repeats': round(sum(r['filled_sec'] for r in info['ambience_scenes'] if not r.get('replacement')) / max(real, 0.01), 2),
                             'library_cycles': round(sum(r['filled_sec'] / r['loop_sec'] for r in info['ambience_scenes'] if r.get('replacement')), 2)}
        info['added_db'] = round(float(10 * np.log10(added_power / max(1, added_count) + 1e-14)), 2)
        missing = sum(r['status'] == 'no clean nonmusical reference' for r in info['ambience_scenes'])
        replacements = sum(bool(r.get('replacement')) for r in info['ambience_scenes'])
        info['reason'] = (f'matched library ambience in {replacements} section(s); {laid:.1f}s filled'
                          if replacements else f'real ambience from the same scene: {laid:.1f}s filled from {real:.1f}s of clean samples (nothing made up)')
        if missing:
            info['reason'] += f'; {missing} scene(s) had no clean ambience reference'
    else:
        Path(part).unlink(missing_ok=True)
        info['reason'] = 'no changing ambience could be restored from clean samples of the same scene'
    return info
