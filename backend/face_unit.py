"""Who speaks on screen: a free check that runs on our own server.

Google MediaPipe Face Landmarker (Apache License 2.0, model cards: Face Mesh V2, BlazeFace short range, Blendshape V2) finds the faces in
each frame and how far each mouth is open. A line is given to the face whose mouth clearly moves while it is spoken; when no visible mouth
moves (a voice-over, a back, a face turned away) the answer is "nobody visible", never a guess.

What the app does with it (longdub_service._face_check / _face_genders):
  * the same face speaking in one continuous shot is the same speaker (a lone line the checks gave to a tiny other speaker comes back);
  * two different faces that are on screen together and both speak as one speaker are marked "check speaker";
  * a small crop of the face that clearly speaks goes to Gemini, which only answers man / woman for that face.

No identification: a face is followed only inside one continuous shot, no face description (embedding) is made or kept, and nothing
says who a person is. Kept with the project: per frame the face boxes and mouth numbers (face_frames.jsonl); the crops sent to Gemini
are temporary files. Any problem here only means the dub goes on without this check.
"""
import base64
import importlib.util
import json
import os
import shutil
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path

_OFF = ('0', 'false', 'off', 'no', '')
ENABLED = os.environ.get('FACE_UNIT', '1').strip().lower() not in _OFF            # on by default; FACE_UNIT=0 switches it off
GENDER = os.environ.get('FACE_GENDER', '1').strip().lower() not in _OFF           # 0: faces still help the speakers, but no gender question to Gemini
MODEL_PATH = Path(os.environ.get('FACE_MODEL') or Path(__file__).resolve().parent / 'models' / 'face_landmarker.task')
VERSION = 1
FPS = 12.0                  # frames looked at per second
WIDTH, HEIGHT = 960, 540
MAX_FACES = 4
CUT = 0.35                  # picture change between two looked-at frames that counts as a new shot
MIN_COVER = 0.4             # a face must be on screen for this share of a line to count
MIN_MOTION = 0.010          # mouth movement (lip gap change per frame, in face heights) that counts as speaking
RIVAL = 0.7                 # a second face moving at least this much of the best one: two mouths move, unclear
MAX_SECONDS = 3600
FRAMES_FILE = 'face_frames.jsonl'
DONE_FILE = 'face_scan.json'
CROPS_PER_SPEAKER = 4
MAX_CROPS = 60

_threads = {}
_lock = threading.Lock()


def available():
    """(True, '') when the check can run here, else (False, the reason in plain words)."""
    if not ENABLED:
        return False, 'switched off (FACE_UNIT=0)'
    if not MODEL_PATH.exists():
        return False, f'model file missing ({MODEL_PATH.name})'
    try:
        if importlib.util.find_spec('mediapipe') is None:
            return False, 'mediapipe is not installed'
    except Exception:
        return False, 'mediapipe is not installed'
    if not shutil.which('ffmpeg'):
        return False, 'ffmpeg is missing'
    return True, ''


def status():
    """For the admin page: 'ok', 'off' (switched off on purpose) or 'fail' (on, but cannot run)."""
    ok, why = available()
    return 'ok' if ok else ('off' if not ENABLED else 'fail')


# ----------------------------------------------------------------------------------------------------------- scanning the video

def _landmarker():
    import mediapipe as mp      # noqa: F401  (imported here: a missing package must never break the app's start)
    from mediapipe.tasks import python as mpt
    from mediapipe.tasks.python import vision
    opts = vision.FaceLandmarkerOptions(base_options=mpt.BaseOptions(model_asset_path=str(MODEL_PATH)),
                                        running_mode=vision.RunningMode.IMAGE, num_faces=MAX_FACES, output_face_blendshapes=True,
                                        min_face_detection_confidence=0.4, min_face_presence_confidence=0.4)
    return vision.FaceLandmarker.create_from_options(opts)


def _face_row(pts, shapes):
    xs = [p.x for p in pts]; ys = [p.y for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    fh = max(1e-6, ((pts[152].x - pts[10].x) ** 2 + (pts[152].y - pts[10].y) ** 2) ** 0.5)       # forehead to chin
    lip = ((pts[14].x - pts[13].x) ** 2 + (pts[14].y - pts[13].y) ** 2) ** 0.5 / fh             # inner lips apart
    bs = {c.category_name: c.score for c in shapes} if shapes else {}
    return dict(box=[round(v, 4) for v in (x0, y0, x1, y1)], lip=round(lip, 4), jaw=round(float(bs.get('jawOpen', 0.0)), 4))


def scan(video, wd, duration=None):
    """Looks at the video (FPS frames a second) and writes face_frames.jsonl + face_scan.json into wd. Returns the summary dict.
    A finished scan is reused. Raises on failure (the caller treats any failure as 'no face check')."""
    wd = Path(wd)
    done = wd / DONE_FILE
    if done.exists() and (wd / FRAMES_FILE).exists():
        try:
            old = json.loads(done.read_text(encoding='utf-8'))
            if old.get('version') == VERSION:
                return old
        except Exception:
            pass
    import numpy as np
    import mediapipe as mp
    lm = _landmarker()
    part = wd / (FRAMES_FILE + '.part')
    cmd = ['ffmpeg', '-v', 'error', '-i', str(video), '-an', '-vf', f'fps={FPS},scale={WIDTH}:{HEIGHT}', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    if duration:
        cmd[4:4] = ['-t', f'{min(float(duration), MAX_SECONDS):.3f}']
    t0 = time.time(); n = 0; faces_seen = 0; prev = None
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = WIDTH * HEIGHT * 3
    try:
        with open(part, 'w', encoding='utf-8') as out:
            while True:
                buf = proc.stdout.read(size)
                if len(buf) < size:
                    break
                rgb = np.frombuffer(buf, np.uint8).reshape(HEIGHT, WIDTH, 3)
                small = rgb[::8, ::8].mean(axis=2)
                hist = np.histogram(small, bins=32, range=(0, 255))[0] / small.size
                cut = float(np.abs(hist - prev).sum()) if prev is not None else 0.0
                prev = hist
                res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
                faces = [_face_row(pts, res.face_blendshapes[k] if res.face_blendshapes else None) for k, pts in enumerate(res.face_landmarks)]
                faces_seen += bool(faces)
                out.write(json.dumps(dict(i=n, cut=round(cut, 4), faces=faces), separators=(',', ':')) + '\n')
                n += 1
                if n >= MAX_SECONDS * FPS:
                    break
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            lm.close()
        except Exception:
            pass
    if n == 0:
        raise RuntimeError('no frames could be read')
    os.replace(part, wd / FRAMES_FILE)
    summary = dict(version=VERSION, frames=n, fps=FPS, frames_with_faces=faces_seen, seconds=round(time.time() - t0, 1))
    done.write_text(json.dumps(summary), encoding='utf-8')
    return summary


def _run(video, wd, duration):
    try:
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)     # behind the separation and transcription, which the customer waits for
        except Exception:
            pass
        scan(video, wd, duration)
    except Exception as ex:
        try:
            (Path(wd) / 'face_scan.error').write_text(f'{type(ex).__name__}: {ex}'[:500], encoding='utf-8')
        except Exception:
            pass
        print(f'[face] scan failed: {type(ex).__name__}: {ex}')


def start(video, wd, duration=None):
    """Starts the scan in the background (once per work folder). Returns True when a scan is running or already done."""
    ok, _ = available()
    if not ok or not Path(video).exists():
        return False
    key = str(Path(wd).resolve())
    with _lock:
        t = _threads.get(key)
        if t is not None and t.is_alive():
            return True
        if (Path(wd) / DONE_FILE).exists():
            return True
        t = threading.Thread(target=_run, args=(video, wd, duration), name='face-scan', daemon=True)
        _threads[key] = t
        t.start()
    return True


def wait(wd, timeout=900):
    """Waits for the scan (at most `timeout` seconds). Returns the frames, or None when there is no finished scan."""
    t = _threads.get(str(Path(wd).resolve()))
    if t is not None:
        t.join(timeout)
    return load(wd)


def load(wd):
    wd = Path(wd)
    if not (wd / DONE_FILE).exists() or not (wd / FRAMES_FILE).exists():
        return None
    try:
        with open(wd / FRAMES_FILE, encoding='utf-8') as f:
            return [json.loads(l) for l in f if l.strip()]
    except Exception:
        return None


# ------------------------------------------------------------------------------------------------------- shots, faces and lines

def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def tracks(frames):
    """Gives every face a track id (the same face followed from frame to frame inside one shot). Returns (shot of each frame,
    {track: [(frame index, face)]}). The frames' faces get a 'track' key."""
    shot_of, shot = [], 0
    for k, f in enumerate(frames):
        if k and f.get('cut', 0) > CUT:
            shot += 1
        shot_of.append(shot)
    out, last, nxt = {}, {}, 0
    for k, f in enumerate(frames):
        used = set()
        for face in f.get('faces') or []:
            best, bid = 0.3, None
            for tid, (box, fk, sh) in last.items():
                if sh != shot_of[k] or k - fk > 6 or tid in used:
                    continue
                v = _iou(face['box'], box)
                if v > best:
                    best, bid = v, tid
            if bid is None:
                bid, nxt = nxt, nxt + 1
            used.add(bid)
            face['track'] = bid
            last[bid] = (face['box'], k, shot_of[k])
            out.setdefault(bid, []).append((k, face))
    return shot_of, out


def judge(frames, rows, fps=FPS):
    """{segment_id: {'kind': 'speaker'|'unclear'|'nobody', 'track', 'shot', 'motion', 'faces' (how many faces were on screen
    long enough), 'alone' (the speaking face was the only one), 'frame' (where its mouth is widest), 'box'}} for every row."""
    if not frames:
        return {}
    shot_of, _ = tracks(frames)
    out = {}
    for r in rows:
        try:
            a, b = int(float(r['start']) * fps), int(float(r['end']) * fps + 0.999)
        except (KeyError, TypeError, ValueError):
            continue
        win = frames[max(0, a):min(len(frames), b + 1)]
        res = dict(kind='nobody', track=None, shot=None, motion=0.0, faces=0, alone=False, frame=None, box=None)
        if win:
            per = {}
            for f in win:
                for face in f.get('faces') or []:
                    per.setdefault(face['track'], []).append((f['i'], face))
            cands = []
            for tid, items in per.items():
                cover = len(items) / len(win)
                if cover < MIN_COVER:
                    continue
                lips = [x['lip'] for _, x in items]
                motion = sum(abs(lips[k + 1] - lips[k]) for k in range(len(lips) - 1)) / max(1, len(lips) - 1)
                widest = max(items, key=lambda it: it[1]['lip'])
                cands.append(dict(track=tid, motion=motion, frame=widest[0], box=widest[1]['box']))
            cands.sort(key=lambda c: -c['motion'])
            res['faces'] = len(cands)
            if cands and cands[0]['motion'] >= MIN_MOTION:
                top = cands[0]
                kind = 'unclear' if len(cands) > 1 and cands[1]['motion'] >= RIVAL * top['motion'] else 'speaker'
                res.update(kind=kind, track=top['track'], shot=shot_of[top['frame']], motion=round(top['motion'], 4),
                           alone=len(cands) == 1, frame=top['frame'], box=top['box'])
        out[r['segment_id']] = res
    return out


def _together(frames, a, b):
    """True when tracks a and b are on screen in the same frames (at least 3): two different people, not one face followed twice."""
    n = 0
    for f in frames:
        ids = {face.get('track') for face in f.get('faces') or []}
        if a in ids and b in ids:
            n += 1
            if n >= 3:
                return True
    return False


def same_face_moves(judged, rows):
    """The same face, alone on screen, speaking several lines in one shot: those lines are one speaker. A line the vote gave to another
    speaker joins the speaker most of that face's lines have (a tie goes to the speaker with more lines in the whole video).
    A move that would leave a speaker with no line is not made (the user's number of speakers stays). Returns [(segment_id, old, new)]."""
    speaker = {r['segment_id']: r['speaker'] for r in rows}
    size = Counter(speaker.values())
    groups = {}
    for sid, j in judged.items():
        if j.get('kind') == 'speaker' and j.get('alone') and sid in speaker:
            groups.setdefault(j['track'], []).append(sid)
    moves = []
    for tid, sids in groups.items():
        if len(sids) < 2:
            continue
        votes = Counter(speaker[s] for s in sids)
        if len(votes) < 2:
            continue
        target = max(votes, key=lambda s: (votes[s], size[s], s))
        for s in sids:
            old = speaker[s]
            if old == target or size[old] <= 1:
                continue
            size[old] -= 1; size[target] += 1; speaker[s] = target
            moves.append((s, old, target))
    return moves


def two_faces(judged, rows, frames):
    """Lines of one speaker that two different faces (on screen together in the same shot) clearly speak: the speaker probably mixes two
    people. Returns {segment_id: speaker} for the lines of the face with fewer lines (both faces' lines when they tie)."""
    speaker = {r['segment_id']: r['speaker'] for r in rows}
    by = {}
    for sid, j in judged.items():
        if j.get('kind') == 'speaker' and sid in speaker:
            by.setdefault((j['shot'], speaker[sid]), {}).setdefault(j['track'], []).append(sid)
    flagged = {}
    for (shot, sp), per in by.items():
        if len(per) < 2:
            continue
        ids = sorted(per, key=lambda t: -len(per[t]))
        mixed = [t for t in ids[1:] if _together(frames, ids[0], t)]
        if not mixed:
            continue
        tie = len(per[ids[0]]) == len(per[mixed[0]])
        for t in (ids if tie else mixed):
            for s in per[t]:
                flagged[s] = sp
    return flagged


# -------------------------------------------------------------------------------------------------------------- gender of faces

def pick_crops(judged, rows, per_speaker=CROPS_PER_SPEAKER, limit=MAX_CROPS):
    """Up to per_speaker lines per speaker where one face clearly speaks: alone on screen first, then the most moving mouth."""
    speaker = {r['segment_id']: r['speaker'] for r in rows}
    by = {}
    for sid, j in judged.items():
        if j.get('kind') == 'speaker' and sid in speaker and j.get('box'):
            by.setdefault(speaker[sid], []).append((not j['alone'], -j['motion'], sid))
    out = []
    for sp in sorted(by):
        for _, _, sid in sorted(by[sp])[:per_speaker]:
            out.append(dict(id=f'f{len(out) + 1}', speaker=sp, segment_id=sid, alone=judged[sid]['alone'],
                            frame=judged[sid]['frame'], box=judged[sid]['box']))
    return out[:limit]


def crop(video, frame, box, out, fps=FPS):
    """A square crop around the face (with some margin), 224 px, as a JPEG file. Returns True on success."""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2 * WIDTH, (y0 + y1) / 2 * HEIGHT
    side = max((x1 - x0) * WIDTH, (y1 - y0) * HEIGHT) * 1.5
    side = max(32, min(side, HEIGHT))
    x = int(max(0, min(WIDTH - side, cx - side / 2))); y = int(max(0, min(HEIGHT - side, cy - side / 2)))
    vf = f'scale={WIDTH}:{HEIGHT},crop={int(side)}:{int(side)}:{x}:{y},scale=224:224'
    try:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{frame / fps:.3f}', '-i', str(video), '-frames:v', '1', '-vf', vf, '-q:v', '4', str(out)],
                       check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return Path(out).exists() and Path(out).stat().st_size > 0
    except Exception:
        return False


PROMPT = ('Each image is one face cut from a film or video. For each image id, say whether this person looks male or female. '
          'Answer unknown when the face is unclear, covered, a young child, or you are not sure. Never name, recognise or identify anyone. '
          'Return JSON only.')
SCHEMA = {'type': 'OBJECT', 'properties': {'faces': {'type': 'ARRAY', 'items': {'type': 'OBJECT', 'properties': {
    'id': {'type': 'STRING'}, 'gender': {'type': 'STRING', 'enum': ['male', 'female', 'unknown']},
    'confidence': {'type': 'STRING', 'enum': ['high', 'low']}}, 'required': ['id', 'gender', 'confidence']}}}, 'required': ['faces']}


def ask_genders(job_id, crops, api_key, ask=None):
    """One Gemini request for all crops (each with its 'path'). Returns ({crop id: 'male'|'female'}, usage report or None).
    Only confident answers count."""
    parts = []
    for c in crops:
        try:
            data = base64.b64encode(Path(c['path']).read_bytes()).decode('ascii')
        except Exception:
            continue
        parts.append({'text': f"Image {c['id']}:"})
        parts.append({'inline_data': {'mime_type': 'image/jpeg', 'data': data}})
    if not parts:
        return {}, None
    parts.append({'text': PROMPT})
    payload = {'contents': [{'parts': parts}],
               'generationConfig': {'responseMimeType': 'application/json', 'responseSchema': SCHEMA, 'thinkingConfig': {'thinkingLevel': 'low'}}}
    if ask is None:
        import gemini_service
        ask = lambda key, p, timeout: gemini_service.call_gemini(key, p, timeout=timeout)
    data, _ = ask(api_key, payload, 120)
    if data is None:
        plain = dict(payload, generationConfig={'responseMimeType': 'application/json'})
        data, _ = ask(api_key, plain, 120)
    try:
        from app_state import record_gemini
        record_gemini(job_id, data)
    except Exception:
        pass
    try:
        text = ''.join(p.get('text', '') for p in data['candidates'][0]['content']['parts'] if isinstance(p, dict) and not p.get('thought'))
        text = text.strip()
        if text.startswith('```'):
            text = '\n'.join(text.splitlines()[1:-1])
        answer = json.loads(text)
        wanted = {c['id'] for c in crops}
        out = {}
        for f in answer.get('faces') or []:
            if isinstance(f, dict) and f.get('id') in wanted and f.get('gender') in ('male', 'female') and f.get('confidence') == 'high':
                out[f['id']] = f['gender']
        usage = data.get('usageMetadata') if isinstance(data, dict) else None
        return out, usage
    except Exception:
        return {}, (data.get('usageMetadata') if isinstance(data, dict) else None)


def speaker_genders(crops, answers):
    """{speaker: {'gender', 'strong', 'seen', 'ids'}} for speakers whose answered crops ALL show the same gender. strong: at least two
    of those crops show the face alone on screen. A crop answered 'unknown' does not count against; one answered otherwise does."""
    by = {}
    for c in crops:
        g = answers.get(c['id'])
        if g:
            by.setdefault(c['speaker'], []).append((g, c['alone'], c['segment_id']))
    out = {}
    for sp, items in by.items():
        genders = {g for g, _, _ in items}
        if len(genders) != 1:
            continue
        alone = sum(1 for _, a, _ in items if a)
        out[sp] = dict(gender=genders.pop(), strong=alone >= 2, seen=len(items), ids=[s for _, _, s in items])
    return out
