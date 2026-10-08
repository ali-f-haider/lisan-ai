"""Leftover temporary voices (lisan-tmp-*) with no project folder are deleted; everything else is left alone."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import inworld_service as iw
import longdub_service as ld

OLD, LIVE = "aaaaaaaa", "bbbbbbbb"


def voices(*rows):
    return [(vid, name) for vid, name in rows]


class OrphanVoiceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup); self.root = Path(folder.name)
        (self.root / (LIVE + "-0000-4000-8000-000000000000")).mkdir()
        for name, value in (("LONG_DIR", self.root), ("INWORLD_API_KEY", "key")):
            patcher = patch.object(ld, name, value); patcher.start(); self.addCleanup(patcher.stop)
        ld._orphan_sweep_at[0] = 0.0
        self.deleted = []
        patcher = patch.object(iw, "delete_voice", side_effect=lambda vid, key: self.deleted.append(vid) or {"ok": True}); patcher.start(); self.addCleanup(patcher.stop)

    def run_sweep(self, listed, **kw):
        with patch.object(iw, "list_voices_with_prefix", return_value=listed):
            return ld.sweep_orphan_voices(**kw)

    def test_only_voices_without_a_project_folder_are_deleted(self):
        listed = voices(("v1", f"lisan-tmp-{OLD}-spk1"), ("v2", f"lisan-tmp-{LIVE}-spk1"), ("v3", "lisan-tmp-weird"), ("v4", f"lisan-tmp-{OLD}-spk2"))
        self.assertEqual(self.run_sweep(listed), 2)
        self.assertEqual(sorted(self.deleted), ["v1", "v4"])

    def test_an_unreadable_voice_list_deletes_nothing(self):
        self.assertEqual(self.run_sweep(None), 0); self.assertEqual(self.deleted, [])

    def test_missing_key_or_project_folder_deletes_nothing(self):
        with patch.object(ld, "INWORLD_API_KEY", ""):
            self.assertEqual(self.run_sweep(voices(("v1", f"lisan-tmp-{OLD}-s"))), 0)
        with patch.object(ld, "LONG_DIR", self.root / "does-not-exist"):
            self.assertEqual(self.run_sweep(voices(("v1", f"lisan-tmp-{OLD}-s"))), 0)
        self.assertEqual(self.deleted, [])

    def test_runs_once_a_day_and_never_more_than_the_cap_at_a_time(self):
        many = voices(*[(f"v{i}", f"lisan-tmp-{OLD}-s{i}") for i in range(80)])
        self.assertEqual(self.run_sweep(many), ld.ORPHAN_SWEEP_MAX)
        self.assertEqual(self.run_sweep(many), 0)
        self.assertEqual(self.run_sweep(many, force=True), ld.ORPHAN_SWEEP_MAX)

    def test_a_voice_that_fails_to_delete_is_not_counted_and_the_rest_continue(self):
        def delete(vid, key):
            self.deleted.append(vid); return {"ok": False, "error": "Inworld error 500"} if vid == "v1" else {"ok": True}
        with patch.object(iw, "delete_voice", side_effect=delete):
            self.assertEqual(self.run_sweep(voices(("v1", f"lisan-tmp-{OLD}-a"), ("v2", f"lisan-tmp-{OLD}-b"))), 1)

    def test_listing_helper_skips_stock_voices_and_other_names_and_reads_all_pages(self):
        pages = [{"voices": [{"voiceId": "a", "displayName": "lisan-tmp-x", "source": "IVC"}, {"voiceId": "b", "displayName": "lisan-tmp-y", "source": "SYSTEM"}],
                  "nextPageToken": "t"}, {"voices": [{"voiceId": "c", "displayName": "Cloned_1", "source": "IVC"}, {"voiceId": "d", "displayName": "lisan-tmp-z", "source": "IVC"}]}]
        with patch.object(iw, "_request", side_effect=pages):
            self.assertEqual(iw.list_voices_with_prefix("key", "lisan-tmp-"), [("a", "lisan-tmp-x"), ("d", "lisan-tmp-z")])
        with patch.object(iw, "_request", side_effect=RuntimeError("down")):
            self.assertIsNone(iw.list_voices_with_prefix("key", "lisan-tmp-"))


if __name__ == "__main__":
    unittest.main()
