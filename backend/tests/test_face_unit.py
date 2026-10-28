"""The face check (face_unit.py) and how the long dub uses it. Offline: frames are made up, Gemini is a fake, no video is read."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import face_unit as F
import longdub_service as ld


def face(x, lip, w=0.2):
    return {'box': [x, 0.2, x + w, 0.6], 'lip': lip, 'jaw': 0.0}


def talking(n, x=0.4, start=0.0):
    """n frames of one face whose mouth opens and closes."""
    return [face(x, 0.02 + (0.05 if k % 2 else 0.0)) for k in range(n)]


def build(spec):
    """spec: list of (cut, [faces]) per frame -> frames with indexes."""
    return [dict(i=k, cut=cut, faces=faces) for k, (cut, faces) in enumerate(spec)]


def row(sid, a, b, sp):
    return {'segment_id': sid, 'start': a, 'end': b, 'speaker': sp, 'text': sid}


class Judge(unittest.TestCase):
    def test_a_moving_mouth_speaks_a_still_one_does_not_and_two_moving_mouths_are_unclear(self):
        fps = F.FPS
        one = build([(0, [f]) for f in talking(24)])                                       # 2 s, one talking face
        still = build([(0, [face(0.4, 0.02)]) for _ in range(24)])                        # a face with a closed mouth
        two = build([(0, [f, dict(f, box=[0.7, 0.2, 0.9, 0.6])]) for f in talking(24)])   # two talking faces
        r = [row('a', 0, 1.5, 'S1')]
        self.assertEqual(F.judge(one, r, fps)['a']['kind'], 'speaker')
        self.assertTrue(F.judge(one, r, fps)['a']['alone'])
        self.assertEqual(F.judge(still, r, fps)['a']['kind'], 'nobody')                  # a voice-over over a silent face: nobody
        self.assertEqual(F.judge(two, r, fps)['a']['kind'], 'unclear')
        self.assertEqual(F.judge([], r), {})

    def test_a_new_shot_is_a_new_face(self):
        spec = [(0, [f]) for f in talking(24)] + [(0.9, [talking(2)[1]])] + [(0, [f]) for f in talking(23)]
        j = F.judge(build(spec), [row('a', 0, 1.5, 'S1'), row('b', 2.2, 3.8, 'S1')])
        self.assertNotEqual(j['a']['track'], j['b']['track'])
        self.assertNotEqual(j['a']['shot'], j['b']['shot'])


class SameFace(unittest.TestCase):
    def frames(self):
        return build([(0, [f]) for f in talking(60)])        # 5 s, one man alone on screen

    def test_guglielmo_goes_back_to_the_speaker_of_the_same_face(self):
        # real run: "Guglielmo." was given to a one-line speaker although the same man says "Grazie." in the same shot
        rows = [row('g1', 0, 1.5, 'S7'), row('g2', 2, 3, 'S9'), row('g3', 3.5, 4.8, 'S7'), row('x', 10, 11, 'S9'), row('y', 12, 13, 'S7')]
        j = F.judge(self.frames(), rows)
        self.assertEqual(F.same_face_moves(j, rows), [('g2', 'S9', 'S7')])

    def test_a_tie_goes_to_the_bigger_speaker_and_a_speaker_is_never_emptied(self):
        rows = [row('a', 0, 1.5, 'S1'), row('b', 2, 3, 'S2'), row('c', 20, 21, 'S1')]
        j = F.judge(self.frames(), rows)
        self.assertEqual(F.same_face_moves(j, rows), [])                              # S2 would be left without a line
        rows.append(row('d', 22, 23, 'S2')); rows.append(row('e', 24, 25, 'S2'))
        j = F.judge(self.frames(), rows)
        self.assertEqual(F.same_face_moves(j, rows), [('a', 'S1', 'S2')])            # 1:1 in the shot; S2 has more lines overall

    def test_with_two_faces_on_screen_nothing_is_moved(self):
        frames = build([(0, [f, dict(face(0.7, 0.02))]) for f in talking(60)])        # the other face's mouth stays closed
        rows = [row('a', 0, 1.5, 'S1'), row('b', 2, 3, 'S2'), row('c', 3.5, 4.8, 'S1'), row('d', 10, 11, 'S2')]
        j = F.judge(frames, rows)
        self.assertEqual(j['b']['kind'], 'speaker'); self.assertFalse(j['b']['alone'])
        self.assertEqual(F.same_face_moves(j, rows), [])                              # a reacting listener could be the one moving


class TwoFaces(unittest.TestCase):
    def test_two_people_on_screen_together_speaking_as_one_speaker_are_marked(self):
        # left face talks for 2 s, then the right face talks; both are on screen the whole time
        left = [(0, [face(0.1, 0.02 + (0.05 if k % 2 else 0)), face(0.6, 0.02)]) for k in range(24)]
        right = [(0, [face(0.1, 0.02), face(0.6, 0.02 + (0.05 if k % 2 else 0))]) for k in range(24)]
        frames = build(left + right)
        rows = [row('m1', 0, 1.8, 'S2'), row('d1', 2.1, 3.9, 'S2')]
        flags = F.two_faces(F.judge(frames, rows), rows, frames)
        self.assertEqual(set(flags), {'m1', 'd1'})                                    # a tie: both are marked
        rows = [row('m1', 0, 1.8, 'S2'), row('d1', 2.1, 3.9, 'S3')]
        self.assertEqual(F.two_faces(F.judge(frames, rows), rows, frames), {})

    def test_one_face_followed_twice_is_not_two_people(self):
        spec = [(0, [f]) for f in talking(24)] + [(0, [])] * 10 + [(0, [dict(f, box=[0.1, 0.2, 0.3, 0.6])]) for f in talking(24)]
        frames = build(spec)
        rows = [row('a', 0, 1.8, 'S1'), row('b', 2.9, 4.8, 'S1')]
        j = F.judge(frames, rows)
        self.assertNotEqual(j['a']['track'], j['b']['track'])
        self.assertEqual(F.two_faces(j, rows, frames), {})                            # never on screen together


class Genders(unittest.TestCase):
    def test_crops_prefer_a_face_alone_and_the_answers_need_to_agree(self):
        judged = {'a': dict(kind='speaker', alone=False, motion=0.05, frame=1, box=[0, 0, 1, 1]),
                  'b': dict(kind='speaker', alone=True, motion=0.02, frame=2, box=[0, 0, 1, 1]),
                  'c': dict(kind='nobody', alone=False, motion=0.0, frame=None, box=None)}
        rows = [row('a', 0, 1, 'S1'), row('b', 1, 2, 'S1'), row('c', 2, 3, 'S1')]
        crops = F.pick_crops(judged, rows)
        self.assertEqual([c['segment_id'] for c in crops], ['b', 'a'])
        two_alone = [dict(id='f1', speaker='S1', alone=True, segment_id='a'), dict(id='f2', speaker='S1', alone=True, segment_id='b'),
                     dict(id='f3', speaker='S2', alone=False, segment_id='c'), dict(id='f4', speaker='S3', alone=True, segment_id='d'),
                     dict(id='f5', speaker='S3', alone=True, segment_id='e')]
        res = F.speaker_genders(two_alone, {'f1': 'female', 'f2': 'female', 'f3': 'male', 'f4': 'male', 'f5': 'female'})
        self.assertEqual((res['S1']['gender'], res['S1']['strong']), ('female', True))
        self.assertEqual((res['S2']['gender'], res['S2']['strong']), ('male', False))
        both_with_others = [dict(id='g1', speaker='S9', alone=False, segment_id='x'), dict(id='g2', speaker='S9', alone=False, segment_id='y')]
        res = F.speaker_genders(both_with_others, {'g1': 'male', 'g2': 'male'})
        self.assertEqual((res['S9']['gender'], res['S9']['strong']), ('male', True))     # the old judge: seen speaking twice, once next to others
        self.assertNotIn('S3', res)                                                   # the faces disagree: no answer

    def test_the_gemini_answer_is_read_safely_and_only_confident_answers_count(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'f1.jpg'; p.write_bytes(b'\xff\xd8jpeg')
            crops = [dict(id='f1', path=str(p)), dict(id='f2', path=str(p))]
            sent = []

            def ask(key, payload, timeout):
                sent.append(payload)
                body = {'faces': [{'id': 'f1', 'gender': 'female', 'confidence': 'high'}, {'id': 'f2', 'gender': 'male', 'confidence': 'low'},
                                  {'id': 'zz', 'gender': 'male', 'confidence': 'high'}]}
                return {'candidates': [{'content': {'parts': [{'text': json.dumps(body)}]}}], 'usageMetadata': {'promptTokenCount': 600}}, None
            out, usage = F.ask_genders('j', crops, 'k', ask=ask)
            self.assertEqual(out, {'f1': 'female'})
            self.assertIn('responseSchema', sent[0]['generationConfig'])
            self.assertIn('Never name, recognise or identify anyone.', sent[0]['contents'][0]['parts'][-1]['text'])
            out, _ = F.ask_genders('j', crops, 'k', ask=lambda *a: (None, 'down'))
            self.assertEqual(out, {})


class Command(unittest.TestCase):
    def test_the_video_path_follows_minus_i_and_the_length_comes_before_it(self):
        # 1.82.86 put the length between -i and the file: ffmpeg read nothing ("no frames could be read") and the face check never ran
        for dur in (None, 488.8, 99999):
            cmd = F.frames_command('/w/src.mp4', dur)
            i = cmd.index('-i')
            self.assertEqual(cmd[i + 1], '/w/src.mp4')
            if dur:
                self.assertLess(cmd.index('-t'), i)
                self.assertLessEqual(float(cmd[cmd.index('-t') + 1]), F.MAX_SECONDS)
            else:
                self.assertNotIn('-t', cmd)


class Switch(unittest.TestCase):
    def test_on_by_default_and_the_switch_turns_it_off(self):
        self.assertTrue(F.ENABLED)
        with mock.patch.object(F, 'ENABLED', False):
            self.assertEqual(F.available(), (False, 'switched off (FACE_UNIT=0)'))
            self.assertEqual(F.status(), 'off')
        with mock.patch.object(F, 'MODEL_PATH', Path('/nowhere/face_landmarker.task')):
            self.assertFalse(F.available()[0])
            self.assertEqual(F.status(), 'fail')


class Wiring(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory(); self.addCleanup(self.d.cleanup)
        self.wd = Path(self.d.name)

    def test_the_check_moves_a_line_to_the_speaker_of_its_face_and_marks_two_faces(self):
        frames = build([(0, [f]) for f in talking(60)])
        rows = [row('g1', 0, 1.5, 'S7'), row('g2', 2, 3, 'S9'), row('g3', 3.5, 4.8, 'S7'), row('x', 10, 11, 'S9')]
        job = {'id': 'j', 'has_video': True, 'ext': '.mp4', 'analysis': {}}
        events = []
        with mock.patch.object(ld, '_face_frames', lambda j, wd: frames), mock.patch.object(ld, '_save', lambda j: None), \
                mock.patch.object(ld, '_ev', lambda job, step, status, detail, *a, **k: events.append((step, detail))):
            s = ld._face_check(job, rows, self.wd)
        self.assertEqual(rows[1]['speaker'], 'S7')
        self.assertEqual(rows[1]['speaker_vote']['source'], 'face')
        self.assertEqual(s['moved'], 1)
        self.assertIn('g2 0:02.0 S9 -> S7', events[0][1])

    def test_genders_follow_the_rules_of_sure_results(self):
        job = {'id': 'j', 'ext': '.mp4', 'speaker_list': [
            {'name': 'S1', 'gender': 'female', 'gender_source': 'pitch', 'gender_uncertain': True},     # a guess: a strong face sets it
            {'name': 'S2', 'gender': 'male', 'gender_source': 'heard', 'gender_uncertain': False},      # a sure listener: a strong face still wins (Ali's rule)
            {'name': 'S3', 'gender': '', 'gender_source': '', 'gender_uncertain': True},                # empty: a weak face fills it, not sure
            {'name': 'S4', 'gender': 'male', 'gender_source': 'chosen', 'gender_uncertain': False},     # the person's choice: never touched
            {'name': 'S5', 'gender': 'male', 'gender_source': 'pitch', 'gender_uncertain': True},       # a weak face that agrees: certain
            {'name': 'S6', 'gender': 'female', 'gender_source': 'pitch', 'gender_uncertain': False},    # a 'sure' pitch: a strong face still wins
            {'name': 'S7', 'gender': 'male', 'gender_source': 'text', 'gender_uncertain': False}]}      # the words: two guaranteed opinions, flagged
        found = {'S1': dict(gender='male', strong=True, seen=2, ids=[]), 'S2': dict(gender='female', strong=True, seen=2, ids=[]),
                 'S3': dict(gender='female', strong=False, seen=1, ids=[]), 'S4': dict(gender='female', strong=True, seen=3, ids=[]),
                 'S5': dict(gender='male', strong=False, seen=1, ids=[]), 'S6': dict(gender='male', strong=True, seen=2, ids=[]),
                 'S7': dict(gender='female', strong=True, seen=3, ids=[])}
        with mock.patch.object(ld, 'GEMINI_API_KEY', 'k'), mock.patch.object(F, 'load', lambda wd: [{'i': 0, 'cut': 0, 'faces': []}]), \
                mock.patch.object(F, 'judge', lambda fr, rows: {}), mock.patch.object(F, 'pick_crops', lambda j, r: [dict(id='f1', frame=0, box=[0, 0, 1, 1])]), \
                mock.patch.object(F, 'crop', lambda *a, **k: True), mock.patch.object(F, 'ask_genders', lambda *a, **k: ({'f1': 'x'}, {})), \
                mock.patch.object(F, 'speaker_genders', lambda c, a: found), mock.patch.object(ld, '_save', lambda j: None), \
                mock.patch.object(ld, '_ev', lambda *a, **k: None):
            ld._face_genders(job, [], self.wd)
        sp = {s['name']: s for s in job['speaker_list']}
        self.assertEqual((sp['S1']['gender'], sp['S1']['gender_source'], sp['S1']['gender_uncertain']), ('male', 'seen', False))
        self.assertEqual((sp['S2']['gender'], sp['S2']['gender_source'], sp['S2']['gender_uncertain']), ('female', 'seen', False))
        self.assertEqual((sp['S7']['gender'], sp['S7']['gender_source'], sp['S7']['gender_uncertain']), ('male', 'text', True))
        self.assertIn('picture_gender_check', sp['S7'])
        self.assertEqual((sp['S3']['gender'], sp['S3']['gender_uncertain']), ('female', True))
        self.assertEqual((sp['S4']['gender'], sp['S4']['gender_source']), ('male', 'chosen'))
        self.assertEqual((sp['S5']['gender'], sp['S5']['gender_uncertain']), ('male', False))
        self.assertEqual((sp['S6']['gender'], sp['S6']['gender_source'], sp['S6']['gender_uncertain']), ('male', 'seen', False))
        self.assertFalse((self.wd / 'face_crops').exists())                         # the face pictures are never kept

    def test_the_admin_list_and_the_privacy_page_name_the_face_check(self):
        root = Path(__file__).resolve().parents[1]
        admin = (root / 'admin.html').read_text(encoding='utf-8')
        for key in ('face', 'fal', 'huggingface', 'whisper', 'pyannote', 'demucs', 'ffmpeg', 'analytics', 'qwen'):
            self.assertIn(f'data-status="{key}"', admin, key)
        privacy = (root / 'privacy.html').read_text(encoding='utf-8')
        self.assertIn('<h2>Faces in your video</h2>', privacy)
        self.assertIn('<h2>الوجوه في الفيديو</h2>', privacy)
        self.assertTrue((root / 'models' / 'face_landmarker.task').exists())
