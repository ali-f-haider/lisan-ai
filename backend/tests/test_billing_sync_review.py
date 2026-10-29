"""An earlier purchase that needs review (made before the current credit ledger) must not stop the credit check or show the
customer an error: a new purchase is still credited and reported. A purchase still pending keeps its message.
Never imports main: billing_sync is taken out of main.py on its own."""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
UID = "11111111-1111-4111-8111-111111111111"


def load(sessions, results):
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "billing_sync")
    node.decorator_list = []
    stripe = NS(api_key=None, checkout=NS(Session=NS(list=lambda **k: NS(data=sessions))))
    env = {"Request": object, "stripe": stripe, "STRIPE_SECRET_KEY": "mock", "_current_uid": lambda _: UID,
           "_fulfill_order": lambda uid, sid, credits: results[sid],
           "_read_subscription_profile": lambda _: {},
           "JSONResponse": lambda body, status_code=200: NS(body=body, status_code=status_code)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), env)
    return env["billing_sync"]


def paid(sid, credits):
    return {"id": sid, "client_reference_id": UID, "payment_status": "paid", "mode": "payment", "metadata": {"credits": str(credits)}}


class EarlierPurchaseUnderReview(unittest.TestCase):
    def test_a_new_purchase_is_credited_although_an_older_one_needs_review(self):
        sync = load([paid("new", 500), paid("old", 100), paid("older", 100)],
                    {"new": 1020, "old": "needs-review", "older": "already-fulfilled"})
        self.assertEqual(sync(object()), {"added_sessions_credits": 500, "needs_review": 1})

    def test_an_older_purchase_under_review_alone_shows_no_error(self):
        sync = load([paid("new", 500), paid("old", 100)], {"new": "already-fulfilled", "old": "needs-review"})
        self.assertEqual(sync(object()), {"added_sessions_credits": 0, "needs_review": 1})

    def test_a_pending_purchase_still_reports_pending(self):
        sync = load([paid("new", 500)], {"new": None})
        self.assertEqual(sync(object()).status_code, 503)


if __name__ == "__main__":
    unittest.main()
