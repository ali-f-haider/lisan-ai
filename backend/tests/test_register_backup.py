"""The voice-number register is copied off-site when it changes and restored when the volume loses it."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import r2_backup
import voice_numbers as vn


class FakeClient:
    def __init__(self):
        self.objects = {}
    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body
    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        body = self.objects[Key]
        return {"Body": type("B", (), {"read": lambda s: body})()}
    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)
    def get_paginator(self, name):
        client = self
        class P:
            def paginate(self, Bucket, Prefix, Delimiter=None):
                if Delimiter:
                    days = sorted({k[len(Prefix):].split("/", 1)[0] for k in client.objects if k.startswith(Prefix)})
                    yield {"CommonPrefixes": [{"Prefix": Prefix + d + "/"} for d in days]}
                else:
                    yield {"Contents": [{"Key": k} for k in list(client.objects) if k.startswith(Prefix)]}
        return P()


class RegisterBackupTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup); self.root = Path(folder.name)
        self.client = FakeClient(); r2_backup._register_sent.clear()
        for target, value in ((r2_backup, "_enabled"), (r2_backup, "_get_client")):
            patcher = patch.object(target, value, (lambda: True) if value == "_enabled" else (lambda: self.client)); patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(vn, "FILE", self.root / "voice_numbers.json"); patcher.start(); self.addCleanup(patcher.stop)
        vn.reset_memory(); self.addCleanup(vn.reset_memory)

    def test_changed_file_is_uploaded_once_and_unchanged_file_is_skipped(self):
        vn.FILE.write_text(json.dumps({"engines": {"e": {"male": {"a": 1}}}}))
        self.assertEqual(r2_backup.backup_register_files([vn.FILE]), 1)
        self.assertEqual(r2_backup.backup_register_files([vn.FILE]), 0)
        vn.FILE.write_text(json.dumps({"engines": {"e": {"male": {"a": 1, "b": 2}}}}))
        self.assertEqual(r2_backup.backup_register_files([vn.FILE]), 1)

    def test_missing_register_is_restored_from_the_newest_copy(self):
        good = {"engines": {"inworld": {"male": {"v9": 17}}}}
        vn.FILE.write_text(json.dumps(good)); r2_backup.backup_register_files([vn.FILE]); vn.FILE.unlink(); vn.reset_memory()
        rows = vn.assign("inworld", [{"voice_id": "v9", "gender": "male"}, {"voice_id": "v10", "gender": "male"}])
        self.assertEqual([r["number"] for r in rows], [17, 18])
        self.assertTrue(vn.FILE.exists())

    def test_without_storage_or_copy_the_register_starts_empty_as_before(self):
        rows = vn.assign("inworld", [{"voice_id": "a", "gender": "male"}])
        self.assertEqual(rows[0]["number"], 1)
        with patch.object(r2_backup, "_enabled", lambda: False):
            vn.FILE.unlink(); vn.reset_memory()
            self.assertEqual(vn.assign("inworld", [{"voice_id": "z", "gender": "male"}])[0]["number"], 1)

    def test_a_damaged_backup_is_ignored(self):
        self.client.objects["register-backups/2026-10-01/voice_numbers.json"] = b"not json"
        self.assertEqual(vn.assign("inworld", [{"voice_id": "a", "gender": "male"}])[0]["number"], 1)

    def test_old_days_are_pruned(self):
        self.client.objects["register-backups/2020-01-01/voice_numbers.json"] = b"{}"
        vn.FILE.write_text(json.dumps({"engines": {}})); r2_backup.backup_register_files([vn.FILE])
        self.assertNotIn("register-backups/2020-01-01/voice_numbers.json", self.client.objects)


if __name__ == "__main__":
    unittest.main()
