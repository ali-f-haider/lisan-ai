"""Dub Long Video from a link (link_fetch.py, 1.100.0)."""
import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import link_fetch as lf


class NormaliseTests(unittest.TestCase):
    def test_drive_file_links_become_the_download_address(self):
        for url in ("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing",
                    "https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp",
                    "https://drive.google.com/uc?id=1AbCdEfGhIjKlMnOp&export=download"):
            out = lf.normalise(url)
            self.assertTrue(out.startswith("https://drive.usercontent.google.com/download?"), out)
            self.assertIn("id=1AbCdEfGhIjKlMnOp", out)
            self.assertIn("confirm=t", out)

    def test_drive_folder_is_refused(self):
        with self.assertRaises(lf.LinkError) as c:
            lf.normalise("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp")
        self.assertEqual(c.exception.key, "drive_folder")

    def test_dropbox_gets_dl_1(self):
        out = lf.normalise("https://www.dropbox.com/scl/fi/abc/film.mp4?rlkey=xyz&dl=0")
        self.assertIn("dl=1", out)
        self.assertNotIn("dl=0", out)
        self.assertIn("rlkey=xyz", out)

    def test_video_sites_and_junk_are_refused(self):
        for url, key in (("https://www.youtube.com/watch?v=abc", "video_site"), ("https://youtu.be/abc", "video_site"),
                         ("https://m.vimeo.com/123", "video_site"), ("ftp://x.com/a.mp4", "bad_link"),
                         ("film.mp4", "bad_link"), ("https://localhost/a.mp4", "bad_link")):
            with self.assertRaises(lf.LinkError) as c:
                lf.normalise(url)
            self.assertEqual(c.exception.key, key, url)

    def test_direct_link_is_kept(self):
        self.assertEqual(lf.normalise(" https://cdn.example.com/a/film.mp4?x=1 "), "https://cdn.example.com/a/film.mp4?x=1")


class SafetyTests(unittest.TestCase):
    def test_private_and_server_addresses_are_refused(self):
        for ip in ("127.0.0.1", "10.0.0.5", "100.64.0.17", "169.254.169.254", "192.168.1.2", "::1", "fd00::1"):
            with patch.object(lf.socket, "getaddrinfo", return_value=[(0, 0, 0, "", (ip, 443))]):
                with self.assertRaises(lf.LinkError, msg=ip):
                    lf._check_public("evil.example.com", 443)

    def test_public_address_passes(self):
        with patch.object(lf.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("142.250.185.78", 443))]):
            lf._check_public("drive.usercontent.google.com", 443)


class _Resp(io.BytesIO):
    def __init__(self, data, headers):
        super().__init__(data)
        self.headers = headers

    def geturl(self):
        return "https://cdn.example.com/x"


class ProbeTests(unittest.TestCase):
    def _probe(self, headers, data=b"x"):
        with patch.object(lf, "open_stream", return_value=_Resp(data, headers)):
            return lf.probe("https://cdn.example.com/x", (".mp4", ".mp3"), 10 ** 9)

    def test_a_page_instead_of_a_file(self):
        with self.assertRaises(lf.LinkError) as c:
            self._probe({"Content-Type": "text/html; charset=utf-8", "Content-Length": "500"})
        self.assertEqual(c.exception.key, "not_public")

    def test_no_size(self):
        with self.assertRaises(lf.LinkError) as c:
            self._probe({"Content-Type": "video/mp4"})
        self.assertEqual(c.exception.key, "no_size")

    def test_name_from_the_header_or_the_type(self):
        _r, name, size = self._probe({"Content-Type": "application/octet-stream", "Content-Length": "1234",
                                      "Content-Disposition": "attachment; filename=\"My Film.mp4\""})
        self.assertEqual((name, size), ("My Film.mp4", 1234))
        _r, name, _s = self._probe({"Content-Type": "video/mp4", "Content-Length": "9"})
        self.assertTrue(name.endswith(".mp4"), name)
        with self.assertRaises(lf.LinkError):
            self._probe({"Content-Type": "application/zip", "Content-Length": "9"})


class _FakeLd:
    CHUNK_BYTES = 4

    def __init__(self, root):
        self.root, self.jobs, self.events = Path(root), {}, []

    def job_dir(self, jid):
        d = self.root / jid
        d.mkdir(exist_ok=True)
        return d

    def load_job(self, jid):
        return self.jobs.get(jid)

    def _lock_for(self, jid):
        return threading.RLock()

    def _save(self, job):
        pass

    def _ev(self, job, step, status, detail=""):
        self.events.append((step, status))


class DownloadTests(unittest.TestCase):
    def _job(self, ld, size):
        job = {"id": "j1", "status": "uploading", "size": size, "total_chunks": -(-size // ld.CHUNK_BYTES), "received": [],
               "link": {"url": "https://x", "kind": "direct", "state": "fetching", "error": ""}}
        ld.jobs["j1"] = job
        return job

    def _wait(self):
        for _ in range(200):
            if not lf.active("j1"):
                return
            time.sleep(0.01)

    def test_whole_file_marks_every_part(self):
        with tempfile.TemporaryDirectory() as t:
            ld = _FakeLd(t)
            job = self._job(ld, 10)
            lf.start(job, _Resp(b"0123456789", {}), ld)
            self._wait()
            self.assertEqual(job["received"], [0, 1, 2])
            self.assertEqual(job["link"]["state"], "done")
            self.assertEqual((Path(t) / "j1" / "src.part").read_bytes(), b"0123456789")
            self.assertEqual(lf.view(job)["state"], "done")

    def test_short_download_fails_and_can_be_retried(self):
        with tempfile.TemporaryDirectory() as t:
            ld = _FakeLd(t)
            job = self._job(ld, 10)
            lf.start(job, _Resp(b"01234", {}), ld)
            self._wait()
            self.assertEqual(job["received"], [])
            v = lf.view(job)
            self.assertEqual((v["state"], v["error"]), ("failed", "stopped"))
            self.assertTrue(v["message"])

    def test_bigger_than_announced_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            ld = _FakeLd(t)
            job = self._job(ld, 4)
            lf.start(job, _Resp(b"0123456789", {}), ld)
            self._wait()
            self.assertEqual(job["link"]["error"], "changed")

    def test_a_download_this_server_is_not_running_counts_as_stopped(self):
        job = {"id": "gone", "status": "uploading", "link": {"state": "fetching"}}
        self.assertEqual(lf.view(job)["error"], "stopped")


if __name__ == "__main__":
    unittest.main()
