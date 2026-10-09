"""R2 keeps a copy of every finished file; the copy must follow the file's retention and nothing else.
Offline: a fake bucket stands in for R2."""
import ast
import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import r2_backup  # noqa: E402

NOW = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.timezone.utc)


def ago(days):
    return NOW - dt.timedelta(days=days)


class FakeBucket:
    def __init__(self, objects, fail=()):
        self.objects, self.fail, self.deleted = dict(objects), set(fail), []

    def get_paginator(self, name):
        bucket = self

        class P:
            def paginate(self, Bucket):
                items = [{"Key": k, "Size": v[0], "LastModified": v[1]} for k, v in bucket.objects.items()]
                yield {"Contents": items[:2]}
                yield {"Contents": items[2:]}
        return P()

    def delete_object(self, Bucket, Key):
        if Key in self.fail:
            raise RuntimeError("network")
        self.deleted.append(Key)
        self.objects.pop(Key, None)


def bucket_with(extra=None, fail=()):
    objs = {
        "old1_final_dubbed_video.mp4": (500, ago(40)),
        "old2_final_voices.m4a": (300, ago(33)),
        "edge_final_dubbed.mp3": (100, ago(31.5)),
        "new_final_dubbed_video.mp4": (700, ago(5)),
        "db-backups/2026-08-01/profiles.json": (50, ago(70)),
        "register-backups/2026-08-01/voices.json": (5, ago(70)),
        "lipsync-tmp/x_video.mp4": (900, ago(70)),
        "notes.txt": (1, ago(70)),
    }
    objs.update(extra or {})
    return FakeBucket(objs, fail)


class PurgeTests(unittest.TestCase):
    def run_purge(self, bucket, **kw):
        with patch.object(r2_backup, "_enabled", return_value=True), patch.object(r2_backup, "_get_client", return_value=bucket), \
                patch.object(r2_backup, "R2_BUCKET_NAME", "b"):
            return r2_backup.purge_old_final_outputs(now=NOW, **kw)

    def test_only_old_finished_copies_are_deleted(self):
        b = bucket_with()
        res = self.run_purge(b)
        self.assertEqual(sorted(b.deleted), ["edge_final_dubbed.mp3", "old1_final_dubbed_video.mp4", "old2_final_voices.m4a"])
        self.assertEqual((res["found"], res["deleted"], res["failed"], res["kept"]), (3, 3, 0, 1))
        self.assertEqual(res["bytes"], 900)

    def test_snapshots_registers_folders_and_other_names_are_never_touched(self):
        b = bucket_with()
        self.run_purge(b, max_age_days=0)
        for safe in ("db-backups/2026-08-01/profiles.json", "register-backups/2026-08-01/voices.json",
                     "lipsync-tmp/x_video.mp4", "notes.txt"):
            self.assertIn(safe, b.objects)

    def test_dry_run_reports_but_deletes_nothing(self):
        b = bucket_with()
        res = self.run_purge(b, dry_run=True)
        self.assertEqual(b.deleted, [])
        self.assertEqual(res["found"], 3)
        self.assertEqual(res["deleted"], 0)

    def test_a_cap_limits_one_run_and_the_next_run_continues(self):
        b = bucket_with()
        self.assertEqual(self.run_purge(b, max_deletes=2)["deleted"], 2)
        self.assertEqual(self.run_purge(b, max_deletes=2)["deleted"], 1)

    def test_one_failed_delete_does_not_stop_the_rest(self):
        b = bucket_with(fail=["old1_final_dubbed_video.mp4"])
        res = self.run_purge(b)
        self.assertEqual((res["deleted"], res["failed"]), (2, 1))

    def test_not_configured_or_bad_age_does_nothing(self):
        with patch.object(r2_backup, "_enabled", return_value=False):
            self.assertIn("error", r2_backup.purge_old_final_outputs())
        b = bucket_with()
        self.assertIn("error", self.run_purge(b, max_age_days=-1))
        self.assertEqual(b.deleted, [])

    def test_a_missing_server_file_alone_never_causes_a_delete(self):
        # the purge has no notion of the server's files: a copy newer than the limit stays, even if the disk was lost
        b = bucket_with()
        self.run_purge(b)
        self.assertIn("new_final_dubbed_video.mp4", b.objects)


class DeleteOneTests(unittest.TestCase):
    def test_deletes_only_finished_names_without_a_folder(self):
        b = bucket_with()
        with patch.object(r2_backup, "_enabled", return_value=True), patch.object(r2_backup, "_get_client", return_value=b), \
                patch.object(r2_backup, "R2_BUCKET_NAME", "b"):
            self.assertTrue(r2_backup.delete_final_output("old1_final_dubbed_video.mp4"))
            self.assertFalse(r2_backup.delete_final_output("db-backups/2026-08-01/profiles.json"))
            self.assertFalse(r2_backup.delete_final_output("notes.txt"))
            self.assertFalse(r2_backup.delete_final_output(""))
        self.assertEqual(b.deleted, ["old1_final_dubbed_video.mp4"])

    def test_failure_is_swallowed_and_reported_as_false(self):
        b = bucket_with(fail=["old1_final_dubbed_video.mp4"])
        with patch.object(r2_backup, "_enabled", return_value=True), patch.object(r2_backup, "_get_client", return_value=b), \
                patch.object(r2_backup, "R2_BUCKET_NAME", "b"):
            self.assertFalse(r2_backup.delete_final_output("old1_final_dubbed_video.mp4"))

    def test_not_configured_is_a_no_op(self):
        with patch.object(r2_backup, "_enabled", return_value=False):
            self.assertFalse(r2_backup.delete_final_output("x_final_dubbed.mp3"))


class WiringTests(unittest.TestCase):
    def _tree(self, name):
        return ast.parse((ROOT / name).read_text(encoding="utf-8-sig"))

    def test_suffix_list_matches_the_one_main_uses_for_local_cleanup(self):
        def literal(tree, target):
            for n in ast.walk(tree):
                if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == target for t in n.targets):
                    return ast.literal_eval(n.value)
        self.assertEqual(literal(self._tree("main.py"), "_FINAL_OUTPUT_SUFFIXES"),
                         literal(self._tree("r2_backup.py"), "FINAL_OUTPUT_SUFFIXES"))

    def test_cleanup_sweep_removes_the_copy_with_the_file_and_runs_the_age_limit(self):
        src = (ROOT / "main.py").read_text(encoding="utf-8-sig")
        body = src[src.index("def _cleanup_worker"):]
        body = body[:body.index("\ndef ", 10)] if "\ndef " in body[10:] else body
        unlink = body.index("p.unlink()")
        self.assertIn("r2_backup.delete_final_output(p.name)", body[unlink:unlink + 400])
        self.assertIn("r2_backup.purge_old_final_outputs()", body)

    def test_project_and_account_deletion_remove_the_copies(self):
        tree = self._tree("longdub_service.py")
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_delete_project_assets")
        self.assertIn("delete_final_output", ast.unparse(fn))

    def test_age_limit_is_longer_than_the_longest_local_retention(self):
        self.assertGreater(r2_backup.FINAL_COPY_MAX_AGE_DAYS, 30)


class ScriptTests(unittest.TestCase):
    def test_dry_run_deletes_nothing_and_yes_deletes(self):
        import r2_cleanup
        b = bucket_with()
        with patch.object(r2_backup, "_enabled", return_value=True), patch.object(r2_backup, "_get_client", return_value=b), \
                patch.object(r2_backup, "R2_BUCKET_NAME", "b"):
            self.assertEqual(r2_cleanup.main(["--older-than-days", "20"]), 0)
            self.assertEqual(b.deleted, [])
            self.assertEqual(r2_cleanup.main(["--older-than-days", "20", "--yes"]), 0)
        self.assertEqual(len(b.deleted), 3)


if __name__ == "__main__":
    unittest.main()
