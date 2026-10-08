"""The worker says whether the provider returned a video; a diagnostic only; the refund depends on whether the customer received a video."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lipsync_service as ls

JOB = "11111111-1111-4111-8111-111111111111"


class ProviderFlagTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup); self.root = Path(folder.name)
        (self.root / "v.mp4").write_bytes(b"v"); (self.root / f"{JOB}_final_dubbed.mp3").write_bytes(b"a")
        for name, value in (("OUTPUT_DIR", self.root), ("LIPSYNC_TEST_MODE", False), ("LIPSYNC_REF_IMAGES_ENABLED", False)):
            patcher = patch.object(ls, name, value); patcher.start(); self.addCleanup(patcher.stop)
        for name, value in (("find_job_video", lambda j: self.root / "v.mp4"), ("compress_video_for_upload", lambda a, b: None),
                            ("job_background_audio", lambda j: None)):
            patcher = patch.object(ls, name, value); patcher.start(); self.addCleanup(patcher.stop)

    def run_worker(self):
        ls.lipsync_worker(JOB, "wan3", "lipsync-2", "", "", "", "key", "ws", "region", "720p", False)
        return ls.jobs_progress[f"lipsync_{JOB}"]

    def test_provider_failure_means_no_video(self):
        with patch.object(ls, "_alibaba_wan3_lipsync", side_effect=RuntimeError("provider said no")):
            progress = self.run_worker()
        self.assertEqual(progress["status"], "error"); self.assertIs(progress["provider_video"], False)

    def test_failure_before_the_provider_means_no_video(self):
        (self.root / f"{JOB}_final_dubbed.mp3").unlink()
        self.assertIs(self.run_worker()["provider_video"], False)

    def test_our_own_finishing_failure_after_a_returned_video_keeps_the_flag_true(self):
        with patch.object(ls, "_alibaba_wan3_lipsync", return_value=None), \
             patch.object(ls, "mux_audio_into_video", side_effect=RuntimeError("mux failed")):
            progress = self.run_worker()
        self.assertEqual(progress["status"], "error"); self.assertIs(progress["provider_video"], True)


if __name__ == "__main__":
    unittest.main()
