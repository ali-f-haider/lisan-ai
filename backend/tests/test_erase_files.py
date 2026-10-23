"""'Delete' means delete: the backup copy goes too, and 'Delete all my files now' removes a customer's files and nothing else."""
import ast
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
MAIN = (BACKEND / "main.py").read_text(encoding="utf-8")
TREE = ast.parse(MAIN)

import datetime as _dt
NOW = _dt.datetime.now(_dt.timezone.utc).isoformat()
OLD = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=60)).isoformat()

JOB = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"


def source_of(name):
    for node in TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(MAIN, node)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.get_source_segment(MAIN, node)
    raise AssertionError(f"{name} not found in main.py")


class FakeResponse:
    def __init__(self, body, status_code=200):
        self.body, self.status_code = body, status_code


class FakeR2:
    def __init__(self):
        self.asked = []
        self.fail = False

    def delete_final_output(self, name):
        self.asked.append(name)
        if self.fail:
            raise RuntimeError("R2 down")
        return True


class FakeApp:
    def post(self, *a, **k):
        return lambda f: f

    def get(self, *a, **k):
        return lambda f: f

    def delete(self, *a, **k):
        return lambda f: f


class World:
    """main.py's erase functions, run for real against a temporary disk with the outside world faked."""
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out, self.up = self.tmp / "outputs", self.tmp / "uploads"
        self.out.mkdir(); self.up.mkdir()
        self.r2 = FakeR2()
        self.progress = {}
        self.active = set()
        self.ns = {
            "OUTPUT_DIR": self.out, "UPLOAD_DIR": self.up, "shutil": shutil, "r2_backup": self.r2,
            "jobs_progress": self.progress, "operation_active": lambda j: j in self.active,
            "_JOB_ID_RE": re.compile(r"[A-Za-z0-9_-]{20,100}"), "JSONResponse": FakeResponse,
            "Request": object, "app": FakeApp(), "customer_message": lambda m: m, "_RATE_LIMIT_MSG": "slow down",
        }
        for name in ("_MY_JOB_FILE_SUFFIXES", "_ERASE_MEDIA_EXTS", "_drop_backup_copy", "_short_job_busy", "_erase_short_job", "_may_still_have_files", "delete_all_my_files"):
            exec(source_of(name), self.ns)

    def touch(self, folder, name, data=b"x"):
        p = folder / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def job_files(self, jid):
        self.touch(self.out, f"{jid}_final_dubbed.mp3")
        self.touch(self.out, f"{jid}_final_dubbed_video.mp4")
        self.touch(self.out, f"{jid}_line_abc_raw.wav")
        self.touch(self.out, f"{jid}_receipt.json", b"{}")
        self.touch(self.up, f"{jid}.mp4")
        self.touch(self.up, f"{jid}_source_language.json", b"{}")
        self.touch(self.up, f"{jid}_separated/htdemucs/no_vocals.wav")

    def names(self, folder):
        return sorted(str(p.relative_to(folder)).replace("\\", "/") for p in folder.rglob("*") if p.is_file())

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class EraseShortJobTests(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.addCleanup(self.w.close)

    def test_removes_finished_files_upload_line_audio_and_separated_tracks_and_asks_r2_for_every_kind(self):
        self.w.job_files(JOB)
        self.w.job_files(OTHER)
        removed = self.w.ns["_erase_short_job"](JOB)
        self.assertEqual(removed, 2)
        left_out = self.w.names(self.w.out)
        left_up = self.w.names(self.w.up)
        self.assertEqual([n for n in left_out if n.startswith(JOB) and not n.endswith(".json")], [])
        self.assertEqual([n for n in left_up if n.startswith(JOB) and not n.endswith(".json")], [])
        self.assertFalse((self.w.up / f"{JOB}_separated").exists())
        self.assertEqual(set(self.w.r2.asked), {JOB + s for s in self.w.ns["_MY_JOB_FILE_SUFFIXES"].values()})

    def test_receipts_and_records_are_never_touched_and_other_jobs_are_left_alone(self):
        self.w.job_files(JOB)
        self.w.job_files(OTHER)
        self.w.touch(self.w.out, f"{JOB}_final_lipsync_receipt.json", b"{}")
        self.w.ns["_erase_short_job"](JOB)
        self.assertTrue((self.w.out / f"{JOB}_receipt.json").exists())
        self.assertTrue((self.w.out / f"{JOB}_final_lipsync_receipt.json").exists())
        self.assertTrue((self.w.up / f"{JOB}_source_language.json").exists())
        self.assertEqual(len([n for n in self.w.names(self.w.out) if n.startswith(OTHER)]), 4)
        self.assertEqual(len([n for n in self.w.names(self.w.up) if n.startswith(OTHER)]), 3)

    def test_a_job_that_is_running_is_left_completely_alone(self):
        self.w.job_files(JOB)
        before = (self.w.names(self.w.out), self.w.names(self.w.up))
        self.w.progress[f"generate_{JOB}"] = {"status": "processing"}
        self.assertIsNone(self.w.ns["_erase_short_job"](JOB))
        self.assertEqual((self.w.names(self.w.out), self.w.names(self.w.up)), before)
        self.assertEqual(self.w.r2.asked, [])

    def test_an_active_lipsync_record_or_operation_also_means_busy(self):
        self.w.job_files(JOB)
        self.w.touch(self.w.out, f"{JOB}_lipsync_active.json", b"{}")
        self.assertIsNone(self.w.ns["_erase_short_job"](JOB))
        (self.w.out / f"{JOB}_lipsync_active.json").unlink()
        self.w.active.add(JOB)
        self.assertIsNone(self.w.ns["_erase_short_job"](JOB))

    def test_odd_ids_delete_nothing(self):
        self.w.job_files(JOB)
        for bad in ("*", "a", "", None, "../" + JOB, JOB[:10]):
            self.assertEqual(self.w.ns["_erase_short_job"](bad), 0)
        self.assertEqual(len(self.w.names(self.w.out)), 4)

    def test_an_unreachable_backup_never_stops_the_local_delete(self):
        self.w.job_files(JOB)
        self.w.r2.fail = True
        self.assertEqual(self.w.ns["_erase_short_job"](JOB), 2)
        self.assertFalse((self.w.out / f"{JOB}_final_dubbed.mp3").exists())


class Long:
    def __init__(self, jid, busy=False, boom=False):
        self.d = {"id": jid, "uid": "u1"}
        self.busy, self.boom = busy, boom

    def get(self, key, default=None):
        return self.d.get(key, default)


class FakeLongDub:
    def __init__(self, projects):
        self.projects, self.deleted = projects, []

    def account_lock(self, uid):
        import threading
        return threading.RLock()

    def list_jobs_for_uid(self, uid):
        return [p for p in self.projects]

    def delete_job(self, project, uid):
        if project.boom:
            raise RuntimeError("disk error")
        if project.busy:
            return False, ("busy", 409)
        self.deleted.append(project.d["id"])
        return True, None


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.addCleanup(self.w.close)
        self.uid = "u1"
        self.limited = False
        self.jobs = {}
        self.ld = FakeLongDub([])
        self.w.ns.update({
            "_current_uid": lambda request: self.uid,
            "_rate_limited": lambda request, bucket, n, window: self.limited,
            "_user_job_ids": lambda uid: self.jobs,
            "longdub_service": self.ld,
            "_storage_summary": lambda uid: {"used_bytes": 0},
        })

    def call(self):
        return self.w.ns["delete_all_my_files"](object())

    def test_must_be_logged_in_and_is_rate_limited_and_needs_the_job_lookup(self):
        self.uid = None
        self.assertEqual(self.call().status_code, 401)
        self.uid = "u1"; self.limited = True
        self.assertEqual(self.call().status_code, 429)
        self.limited = False; self.jobs = None
        self.assertEqual(self.call().status_code, 503)

    def test_deletes_short_files_and_long_projects_and_counts_them(self):
        short, long_id = JOB, OTHER
        self.w.job_files(short)
        self.w.job_files(long_id)                    # leftovers named like the long project must stay: the project delete owns them
        self.jobs = {short: NOW, long_id: NOW}
        self.ld.projects = [Long(long_id)]
        out = self.call()
        self.assertEqual(out["status"], "success")
        self.assertEqual((out["files_removed"], out["projects_removed"], out["skipped_busy"]), (2, 1, 0))
        self.assertEqual(self.ld.deleted, [long_id])
        self.assertFalse((self.w.out / f"{short}_final_dubbed.mp3").exists())
        self.assertTrue((self.w.out / f"{long_id}_final_dubbed.mp3").exists())

    def test_busy_or_failing_projects_and_running_short_jobs_are_kept_and_reported(self):
        self.w.job_files(JOB)
        self.w.progress[f"merge_{JOB}"] = {"status": "processing"}
        self.jobs = {JOB: NOW, "bbbbbbbb-1111-2222-3333-444444444444": NOW}
        self.ld.projects = [Long("lllllllllllllllllllllll1", busy=True), Long("lllllllllllllllllllllll2", boom=True)]
        out = self.call()
        self.assertEqual(out["skipped_busy"], 3)
        self.assertEqual(out["projects_removed"], 0)
        self.assertTrue((self.w.out / f"{JOB}_final_dubbed.mp3").exists())

    def test_jobs_older_than_every_retention_limit_are_skipped_so_a_big_history_cannot_make_the_request_crawl(self):
        self.w.job_files(JOB)
        self.jobs = {JOB: OLD, "cccccccc-1111-2222-3333-444444444444": OLD}
        out = self.call()
        self.assertEqual(out["files_removed"], 0)
        self.assertEqual(self.w.r2.asked, [])
        self.assertTrue((self.w.out / f"{JOB}_final_dubbed.mp3").exists())      # (cannot really exist that old: the point is that it is not even looked at)

    def test_unreadable_dates_count_as_recent(self):
        fn = self.w.ns["_may_still_have_files"]
        self.assertTrue(fn(""))
        self.assertTrue(fn(None))
        self.assertTrue(fn("garbage"))
        self.assertTrue(fn(NOW))
        self.assertTrue(fn(NOW.replace("+00:00", "Z")))
        self.assertFalse(fn(OLD))

    def test_only_this_users_jobs_are_ever_looked_at(self):
        # the ids come from _user_job_ids(uid) and list_jobs_for_uid(uid); nothing else is enumerated
        src = source_of("delete_all_my_files")
        self.assertIn("_user_job_ids(uid)", src)
        self.assertIn("list_jobs_for_uid(uid)", src)
        self.assertNotIn("glob(", src)


class WiringTests(unittest.TestCase):
    def test_deleting_one_file_and_deleting_the_account_also_remove_the_backup_copy(self):
        one = source_of("delete_my_job")
        self.assertIn("_drop_backup_copy(", one)
        acct = source_of("_delete_account_run")
        self.assertIn("_drop_backup_copy(", acct)

    def test_the_backup_copy_goes_even_when_the_server_file_was_already_gone(self):
        one = source_of("delete_my_job")
        loop = one[one.index("for suffix"):one.index("try:\n        sm")]
        # the call sits at loop level, outside the `if p.exists()` block
        self.assertRegex(loop, r"\n        _drop_backup_copy\(")

    def test_account_page_has_the_button_the_dialog_and_both_languages(self):
        page = (BACKEND / "account.html").read_text(encoding="utf-8")
        self.assertIn('id="eraseAllBtn"', page)
        self.assertIn('fetch("/api/account/delete_files", { method: "POST" })', page)
        self.assertIn("LisanDialog.confirm(T.eraseConfirm", page)
        for key in ("eraseHeading", "eraseNote", "eraseBtn", "eraseConfirm", "eraseDone", "eraseSkipped", "eraseFailed"):
            self.assertEqual(page.count(f"    {key}:"), 2, key)        # one English, one Arabic

    def test_the_no_training_line_is_where_people_upload_and_where_they_pay(self):
        self.assertIn("never use your media to train AI models", (BACKEND / "index.html").read_text(encoding="utf-8"))
        self.assertIn("noTrainNote", (BACKEND / "app.js").read_text(encoding="utf-8"))
        self.assertIn("ldNoTrain", (BACKEND / "dub_long.html").read_text(encoding="utf-8"))
        pricing = (BACKEND / "pricing.html").read_text(encoding="utf-8")
        self.assertIn("We never train AI on your media", pricing)
        self.assertIn("لا ندرّب الذكاء الاصطناعي على وسائطك", pricing)

    def test_privacy_page_says_exactly_what_the_buttons_do_in_both_languages(self):
        privacy = (BACKEND / "privacy.html").read_text(encoding="utf-8")
        self.assertIn("together with its copy in our backup storage", privacy)
        self.assertIn("Delete all my files now", privacy)
        self.assertIn("مع نسخته في التخزين الاحتياطي", privacy)
        self.assertIn("احذف كل ملفاتي الآن", privacy)
        self.assertIn("we do not use your content to train AI models", privacy)


if __name__ == "__main__":
    unittest.main()
