"""Admin "Raise the prices by a factor": the server keeps the ORIGINAL prices and the factor last applied, so a new factor
replaces the old one (×6 after ×5 is original × 6, not × 30) and the admin page opens with the factor last used.
Never imports main: the functions are taken out of main.py on their own."""
import ast
import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]


def load(data_dir):
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    keep = []
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") in ("PRICE_FACTOR_FILE", "PRICE_FACTOR_IDS") for t in n.targets):
            keep.append(n)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in (
                "_price_factor_state", "admin_get_price_factor", "admin_save_price_factor"):
            n.decorator_list = []
            keep.append(n)
    env = {"DATA_DIR": Path(data_dir), "json": json, "os": os, "time": time, "_admin_check": lambda r: True,
           "Request": object, "JSONResponse": lambda body, status_code=200: NS(body=body, status_code=status_code)}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "main.py", "exec"), env)
    return env


def post(env, body):
    async def read():
        return body
    return asyncio.run(env["admin_save_price_factor"](NS(json=read)))


class PriceFactor(unittest.TestCase):
    def test_nothing_saved_yet_means_factor_one(self):
        with tempfile.TemporaryDirectory() as d:
            env = load(d)
            self.assertEqual(env["admin_get_price_factor"](None), {"factor": 1.0, "base": {}, "saved_at": None})

    def test_the_factor_and_the_original_prices_are_kept(self):
        with tempfile.TemporaryDirectory() as d:
            env = load(d)
            base = {"transcribeCredits": 5, "mergeCredits": 2, "inworldCharsPerCredit": 50, "longDubAnalysisPerMin": 19}
            r = post(env, {"factor": 5, "base": base})
            self.assertTrue(r["ok"])
            r = post(env, {"factor": 6, "base": base})          # a new factor replaces the old one
            got = env["admin_get_price_factor"](None)
            self.assertEqual(got["factor"], 6.0)
            self.assertEqual(got["base"], {k: float(v) for k, v in base.items()})
            self.assertTrue((Path(d) / "price_factor.json").exists())

    def test_bad_input_is_refused_and_nothing_changes(self):
        with tempfile.TemporaryDirectory() as d:
            env = load(d)
            post(env, {"factor": 2, "base": {"mergeCredits": 2}})
            for bad in ({"factor": 0.5, "base": {}}, {"factor": 500, "base": {}}, {"factor": "x"},
                        {"factor": 3, "base": {"mergeCredits": -1}}):
                self.assertEqual(post(env, bad).status_code, 400)
            self.assertEqual(env["admin_get_price_factor"](None)["factor"], 2.0)

    def test_unknown_fields_are_not_stored(self):
        with tempfile.TemporaryDirectory() as d:
            env = load(d)
            post(env, {"factor": 2, "base": {"mergeCredits": 2, "lipsyncCreditsPerSec": 40, "evil": 1}})
            self.assertEqual(env["admin_get_price_factor"](None)["base"], {"mergeCredits": 2.0})

    def test_the_admin_page_applies_the_factor_to_the_original_prices(self):
        page = (ROOT / "admin.html").read_text(encoding="utf-8")
        self.assertIn("/api/admin/price_factor", page)
        self.assertIn("await pcLoadFactor();", page)
        self.assertIn("await savePricing(f);", page)
        self.assertNotIn("Saving again with a factor multiplies the new prices again", page)


if __name__ == "__main__":
    unittest.main()
