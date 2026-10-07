"""The Auto-Assign service: speakers are measured and listened to, voices come back best-first."""
import os, sys, tempfile, unittest, wave
from pathlib import Path
from unittest.mock import patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice_match as vm
import voice_match_service as svc

SR = 16000
# who speaks when (the fake audio uses a different pitch for each range)
F0_AT = lambda start: 105 if start < 100 else 230


def tone(f0, seconds):
    t = np.arange(int(seconds * SR)) / SR
    ph = 2 * np.pi * f0 * t
    x = sum((1.0 / h) * np.sin(h * ph) for h in range(1, 12))
    return (x / np.abs(x).max() * 0.4 * (0.6 + 0.4 * np.sin(2 * np.pi * 2.5 * t) ** 2)).astype(np.float32)


def write(path, x):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes((x * 32767).astype("<i2").tobytes())


def fake_cut(src, start, dur, out):
    if str(out).endswith(".mp3"):
        Path(out).write_bytes(b"mp3")
        return
    write(out, tone(F0_AT(start), dur))


VOICES = [dict(voice_id="m_young", gender="male", age="young", descriptive="bright"),
          dict(voice_id="m_old", gender="male", age="old", descriptive="deep, calm"),
          dict(voice_id="f_young", gender="female", age="young", descriptive="warm"),
          dict(voice_id="f_old", gender="female", age="old")]
SEGS = [dict(speaker="Grandpa", start=float(i * 5), end=float(i * 5 + 4)) for i in range(6)] + \
       [dict(speaker="Lily", start=float(100 + i * 5), end=float(100 + i * 5 + 4)) for i in range(4)]
SPEAKERS = [dict(name="Grandpa", gender="", age=""), dict(name="Lily", gender="", age="")]


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self.tmp.name
        self.work = Path(self.tmp.name) / "w"
        self.asked = []

    def tearDown(self):
        os.environ.pop("DATA_DIR", None); self.tmp.cleanup()

    def describe(self, job, mp3):
        self.asked.append(mp3.name)
        return {"gender": "male", "age": "senior", "tone": ["deep", "calm"], "uncertain": False}

    def test_voices_follow_the_speakers_age_and_pitch(self):
        def describe(job, mp3):
            self.asked.append(1)
            return ({"gender": "male", "age": "senior", "tone": ["deep"], "uncertain": False} if len(self.asked) == 1
                    else {"gender": "female", "age": "young", "tone": ["warm"], "uncertain": False})
        r = svc.match("j", "src", SPEAKERS, SEGS, VOICES, cut=fake_cut, describe=describe, work=self.work)
        g, l = r["speakers"]["Grandpa"], r["speakers"]["Lily"]
        self.assertEqual((g["gender"], g["age"], g["age_source"], g["best"]), ("male", "senior", "heard", "m_old"))
        self.assertEqual((l["gender"], l["age"], l["best"]), ("female", "young", "f_young"))
        self.assertEqual(r["listener"], "used")
        self.assertAlmostEqual(g["pitch_hz"] / 105, 1, delta=0.05)

    def test_without_the_listener_pitch_still_finds_the_gender(self):
        r = svc.match("j", "src", SPEAKERS, SEGS, VOICES, cut=fake_cut, describe=None, work=self.work)
        self.assertEqual(r["listener"], "off")
        g, l = r["speakers"]["Grandpa"], r["speakers"]["Lily"]
        self.assertEqual((g["gender"], g["gender_source"], g["age"]), ("male", "pitch", None))
        self.assertTrue(g["best"].startswith("m_")); self.assertTrue(l["best"].startswith("f_"))

    def test_a_failing_listener_never_stops_the_matching(self):
        def broken(job, mp3):
            raise ValueError("down")
        r = svc.match("j", "src", SPEAKERS, SEGS, VOICES, cut=fake_cut, describe=broken, work=self.work)
        self.assertEqual(r["listener"], "unavailable")
        self.assertTrue(r["speakers"]["Grandpa"]["best"])

    def test_the_persons_choices_win_over_what_was_heard(self):
        sp = [dict(name="Grandpa", gender="female", age="young"), dict(name="Lily", gender="", age="")]
        r = svc.match("j", "src", sp, SEGS, VOICES, cut=fake_cut, describe=self.describe, work=self.work)
        g = r["speakers"]["Grandpa"]
        self.assertEqual((g["gender"], g["gender_source"], g["age"], g["age_source"], g["best"]), ("female", "chosen", "young", "chosen", "f_young"))

    def test_a_speaker_with_almost_no_speech_still_gets_a_voice_without_a_listening_call(self):
        sp = [dict(name="Cameo", gender="female", age="")]
        r = svc.match("j", "src", sp, [dict(speaker="Cameo", start=200.0, end=200.5)], VOICES, cut=fake_cut, describe=self.describe, work=self.work)
        self.assertEqual(self.asked, [])
        self.assertTrue(r["speakers"]["Cameo"]["best"].startswith("f_"))
        self.assertIsNone(r["speakers"]["Cameo"]["pitch_hz"])

    def test_listening_is_limited_to_a_few_speakers(self):
        many = [dict(name=f"S{i}", gender="", age="") for i in range(9)]
        segs = [dict(speaker=f"S{i}", start=float(i * 10), end=float(i * 10 + 4)) for i in range(9)]
        svc.match("j", "src", many, segs, VOICES, cut=fake_cut, describe=self.describe, work=self.work)
        self.assertEqual(len(self.asked), svc.MAX_LISTENED_SPEAKERS)

    def test_temporary_files_are_removed(self):
        svc.match("j", "src", SPEAKERS, SEGS, VOICES, cut=fake_cut, describe=self.describe, work=self.work)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_library_previews_are_measured_and_used(self):
        voices = [dict(voice_id="a", gender="male", age="adult", preview_url="https://x/105"),
                  dict(voice_id="b", gender="male", age="adult", preview_url="https://x/150")]
        def fetch(url):
            p = self.work / (url.split("/")[-1] + ".wav"); write(p, tone(int(url.split("/")[-1]), 3)); return p
        r = svc.match("j", "src", [dict(name="Grandpa", gender="male", age="")], SEGS, voices, cut=fake_cut, fetch_wav=fetch, work=self.work)
        self.assertEqual(r["speakers"]["Grandpa"]["best"], "a")          # the voice whose measured pitch is nearest to 105 Hz
        self.assertEqual(r["measured_voices"], 2)


class FetcherTests(unittest.TestCase):
    def test_only_https_and_small_files(self):
        with tempfile.TemporaryDirectory() as d:
            f = svc.make_wav_fetcher(d, lambda a, b: Path(b).write_bytes(b"x"))
            with self.assertRaises(ValueError):
                f("http://x/y.mp3")
            class R:
                def __init__(s, n): s.n = n
                def read(s, k): return b"a" * min(k, s.n)
                def __enter__(s): return s
                def __exit__(s, *a): pass
            with patch("urllib.request.urlopen", lambda url, timeout: R(5_000_000)):
                with self.assertRaises(ValueError):
                    f("https://x/y.mp3")
            with patch("urllib.request.urlopen", lambda url, timeout: R(100)):
                out = f("https://x/y.mp3")
            self.assertTrue(Path(out).exists())
            self.assertEqual([p.suffix for p in Path(d).iterdir()], [".wav"])      # the raw download is gone


class VoiceListTests(unittest.TestCase):
    def setUp(self): svc.reset_cache()
    def tearDown(self): svc.reset_cache()

    def test_the_list_is_cached_and_a_failure_is_not(self):
        calls = []
        def ok():
            calls.append(1); return {"voices": [{"voice_id": "a"}]}
        self.assertEqual(svc.library_voices(ok), [{"voice_id": "a"}]); svc.library_voices(ok)
        self.assertEqual(len(calls), 1)
        svc.reset_cache()
        self.assertEqual(svc.library_voices(lambda: {"error": "x"}), [])
        self.assertEqual(svc.library_voices(lambda: (_ for _ in ()).throw(OSError())), [])
        self.assertEqual(svc.library_voices(ok), [{"voice_id": "a"}])       # not stuck on the failure


if __name__ == "__main__":
    unittest.main()
