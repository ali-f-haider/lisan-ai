"""Saving a project's lines must be safe when two saves happen at the same moment.

A speaker rename and a line edit (or a double click) used to share one temporary file name, so the second rename failed
with "No such file or directory" and the person saw a server error. These tests take the real functions out of the
source (no heavy imports), so they run on any machine."""
import ast
import json
import os
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "longdub_service.py"


def _tree():
    return ast.parse(SRC.read_text(encoding="utf-8"))


def _function(name):
    for node in _tree().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(name + " not found")


class SegmentWrites(unittest.TestCase):
    def _writer(self, folder):
        module = ast.Module(body=[_function("_seg_path"), _function("_write_segments")], type_ignores=[])
        space = {"json": json, "os": os, "uuid": uuid, "Path": Path, "_wd": lambda job: folder}
        exec(compile(ast.fix_missing_locations(module), str(SRC), "exec"), space)
        return space["_write_segments"]

    def test_many_saves_at_once_never_fail_and_leave_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write = self._writer(folder)
            errors = []

            def worker(n):
                try:
                    for k in range(200):
                        write({"id": "job"}, [{"segment_id": f"s{n}-{k}", "text": "نص"}])
                except Exception as ex:                      # noqa: BLE001 - the test reports whatever happened
                    errors.append(f"{type(ex).__name__}: {ex}")

            threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            self.assertEqual(list(folder.glob("*.tmp")), [])
            rows = json.loads((folder / "segments.json").read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)                   # always one complete, readable file

    def test_a_failed_write_leaves_the_old_lines_and_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write = self._writer(folder)
            write({"id": "job"}, [{"segment_id": "old"}])
            with self.assertRaises(TypeError):
                write({"id": "job"}, [{"segment_id": object()}])     # cannot be saved as JSON
            self.assertEqual(json.loads((folder / "segments.json").read_text(encoding="utf-8")), [{"segment_id": "old"}])
            self.assertEqual(list(folder.glob("*.tmp")), [])

    def test_renaming_speakers_holds_the_project_lock_like_every_other_line_edit(self):
        fn = _function("set_speakers")
        locked = [n for n in ast.walk(fn) if isinstance(n, ast.With)
                  and any("_lock_for" in ast.dump(item.context_expr) for item in n.items)]
        self.assertTrue(locked, "set_speakers must read, change and write the lines under _lock_for")
        inside = {ast.dump(c.func) for w in locked for c in ast.walk(w) if isinstance(c, ast.Call)}
        for needed in ("read_segments", "_write_segments"):
            self.assertTrue(any(needed in d for d in inside), needed + " must be inside the lock")


if __name__ == "__main__":
    unittest.main()
