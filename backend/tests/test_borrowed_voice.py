"""A speaker with too little speech to copy borrows a voice of their own gender (8 Nov 2026: unknownWoman 1 got a man's voice)."""
import unittest
from pathlib import Path
from unittest import mock

import longdub_service as ld

ROOT = Path(__file__).resolve().parents[1]


class Donor(unittest.TestCase):
    rows = [{"speaker_id": "sp1", "gender": "male"}, {"speaker_id": "sp1", "gender": "male"}, {"speaker_id": "sp2", "gender": "male"},
            {"speaker_id": "sp9", "gender": "male"}]          # the woman's lines were moved to her from a man: their old marks say male

    def test_the_speaker_list_decides_the_gender(self):
        job = {"speaker_list": [{"id": "sp1", "gender": "male"}, {"id": "sp2", "gender": "female", "gender_source": "chosen"},
                                {"id": "sp9", "gender": "female", "gender_source": "chosen"}]}
        self.assertEqual(ld._pick_donor("sp9", ["sp1", "sp2"], self.rows, job), "sp2")
        self.assertEqual(ld._pick_donor("sp9", ["sp1", "sp2"], self.rows), "sp1")       # without the list: the old behaviour

    def test_no_copied_voice_of_that_gender_means_a_stock_voice_of_it(self):
        lib = {"voices": [{"voice_id": "m1", "gender": "male", "arabic": True, "name": "Omar"},
                          {"voice_id": "f2", "gender": "female", "arabic": False, "name": "Ann"},
                          {"voice_id": "f1", "gender": "female", "arabic": True, "name": "Nour"}]}
        with mock.patch("inworld_service.fetch_library", lambda key: lib):
            self.assertEqual(ld._stock_voice("female", "k")["voice_id"], "f1")
        with mock.patch("inworld_service.fetch_library", lambda key: {"error": "down"}):
            self.assertIsNone(ld._stock_voice("female", "k"))

    def test_the_dub_uses_it(self):
        src = (ROOT / "longdub_service.py").read_text(encoding="utf-8")
        part = src.split("donor = _pick_donor(spid, good, rows, job)")[1][:900]
        self.assertIn("stock = _stock_voice(want, INWORLD_API_KEY)", part)
        self.assertIn('dub["fallback"][spid] = stock["voice_id"] if stock else dub["voices"][donor]', part)


if __name__ == "__main__":
    unittest.main()
