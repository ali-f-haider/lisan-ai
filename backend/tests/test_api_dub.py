"""The paid long-dub endpoints of the public API, against a fake database and a fake of the website's route code.
Every money path has a test: charge exactly once, ceilings, daily cap, retries, uncertain payments."""
import itertools
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api_dub
import api_v1
from api_fake_ld import FakeLD, CHUNK
from api_testkit import Harness, OWNER, OTHER

TERMS = api_v1.TERMS_VERSION
_n = itertools.count(1)


def idem():
    return "idem-%016d" % next(_n)


class Base(unittest.TestCase):
    def setUp(self):
        self.ld = FakeLD(credits=1000)
        self.h = Harness(ld=self.ld, requests_per_minute=6000, burst=1000, concurrency_per_key=1)
        self.key = self.h.make_key(scopes=("read", "dub", "account"), cap=500)
        self.hd = self.h.bearer(self.key)

    # ---- tiny client ------------------------------------------------------------------------------
    def post(self, path, body=None, key=None, headers=None, k=None):
        h = dict(headers or self.hd)
        h["Idempotency-Key"] = k or idem()
        return self.h.c.post("/v1" + path, json=body, headers=h)

    def get(self, path, headers=None):
        return self.h.c.get("/v1" + path, headers=headers or self.hd)

    def create(self, size=2500, **over):
        body = {"kind": "long", "filename": "clip.mp4", "size_bytes": size, "rights_confirmed": True, "terms_version": TERMS}
        body.update(over)
        return self.post("/jobs", body)

    def upload(self, job_id, size=2500):
        for i in range((size + CHUNK - 1) // CHUNK):
            n = min(CHUNK, size - i * CHUNK)
            r = self.h.c.put("/v1/jobs/%s/upload/chunks/%d" % (job_id, i), content=b"x" * n,
                             headers=dict(self.hd, **{"Idempotency-Key": idem(), "Content-Type": "application/octet-stream"}))
            self.assertEqual(r.status_code, 200, r.text)

    def quote(self, job_id, step, **extra):
        r = self.post("/jobs/%s/quotes" % job_id, dict({"step": step}, **extra))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def approve_body(self, q, **over):
        b = {"quote_id": q["id"], "quoted_credits": q["quoted_credits"], "max_credits": q["quoted_credits"], "terms_version": TERMS}
        b.update(over)
        return b

    def pay(self, job_id, q, over=None, k=None, headers=None):
        path = "/jobs/%s/upload/finish" % job_id if q["step"] == "estimate" else "/jobs/%s/accept" % job_id
        return self.post(path, self.approve_body(q, **(over or {})), k=k, headers=headers)

    def job_to_estimate(self):
        jid = self.create().json()["id"]
        self.upload(jid)
        q = self.quote(jid, "estimate")
        r = self.pay(jid, q)
        self.assertEqual(r.status_code, 202, r.text)
        return jid

    def job_to_editing(self):
        jid = self.job_to_estimate()
        q = self.quote(jid, "analysis")
        r = self.pay(jid, q)
        self.assertEqual(r.status_code, 202, r.text)
        self.ld.jobs[jid].update(status="editing", stage="review")
        return jid

    def drift(self):
        return self.h.limiter


class HappyPath(Base):
    def test_whole_flow_charges_each_slot_once(self):
        r = self.create()
        self.assertEqual(r.status_code, 201, r.text)
        jid = r.json()["id"]
        self.assertEqual(r.json()["status"], "uploading")
        self.upload(jid)
        up = self.get("/jobs/%s/upload" % jid).json()
        self.assertEqual(up["received_count"], 3)
        q = self.quote(jid, "estimate")
        self.assertEqual((q["quoted_credits"], q["additional_max_credits"]), (3, 0))
        r = self.pay(jid, q)
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(r.json()["status"], "estimated")
        self.assertEqual(r.json()["current_operation"]["credits_debited"], 3)
        est = self.get("/jobs/%s/estimate" % jid).json()
        self.assertEqual(est["already_paid_credits"], 3)
        q2 = self.quote(jid, "analysis")
        self.assertEqual(q2["quoted_credits"], 20)           # 10 analysis + 10 flat
        r = self.pay(jid, q2)
        self.assertEqual(r.status_code, 202, r.text)
        self.ld.jobs[jid].update(status="editing", stage="review")
        q3 = self.quote(jid, "dub")
        self.assertEqual(q3["fixed_credits"], 20 + 10 + 1)
        self.assertEqual(q3["additional_max_credits"], 20)
        self.assertEqual(q3["quoted_credits"], 51)
        r = self.pay(jid, q3)
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(r.json()["status"], "confirmed")
        self.assertEqual(self.ld.credits, 1000 - 3 - 20 - 31)
        self.assertEqual(sorted(self.ld.ledger.values()), [3, 20, 31])
        self.ld.jobs[jid].update(status="done", stage="done")
        res = self.get("/jobs/%s/results" % jid).json()["results"]
        self.assertEqual([x["kind"] for x in res], ["mixed", "dialogue"])
        self.assertEqual(self.get("/jobs/%s/results/mixed" % jid).content, b"abc")
        self.assertEqual(self.get("/jobs/%s/results/background" % jid).status_code, 410)
        self.assertEqual(self.get("/jobs/%s/results/other" % jid).status_code, 404)

    def test_job_is_tagged_with_key_and_price(self):
        jid = self.create().json()["id"]
        a = self.ld.jobs[jid]["api"]
        self.assertEqual((a["price_percent"], a["revision"], a["terms_version"]), (100, 0, TERMS))
        self.assertTrue(a["key_id"])

    def test_price_percent_applies_to_quotes(self):
        self.h.settings["price_percent"] = 200
        jid = self.create().json()["id"]
        self.upload(jid)
        q = self.quote(jid, "estimate")
        self.assertEqual(q["quoted_credits"], 6)

    def test_list_and_pagination(self):
        ids = [self.create().json()["id"] for _ in range(3)]
        self.ld.jobs["web-only"] = {"id": "web-only", "uid": OWNER, "status": "done", "created": 1}      # website project: never listed
        page = self.get("/jobs?limit=2").json()
        self.assertEqual(len(page["jobs"]), 2)
        self.assertTrue(page["next_cursor"])
        rest = self.get("/jobs?limit=2&cursor=" + page["next_cursor"]).json()
        self.assertEqual(len(rest["jobs"]), 1)
        self.assertIsNone(rest["next_cursor"])
        got = [j["id"] for j in page["jobs"] + rest["jobs"]]
        self.assertEqual(sorted(got), sorted(ids))
        self.assertEqual(self.get("/jobs?limit=0").status_code, 400)
        self.assertEqual(self.get("/jobs?limit=2&cursor=bad").status_code, 400)


class Validation(Base):
    def test_create_rules(self):
        self.assertEqual(self.create(kind="short").status_code, 400)
        r = self.create(terms_version="2000-01-01")
        self.assertEqual(r.json()["error"]["code"], "consent_required")
        self.assertEqual(self.create(rights_confirmed=False).status_code, 400)
        self.assertEqual(self.create(filename="../a.mp4").status_code, 400)

    def test_mutations_need_an_idempotency_key(self):
        body = {"kind": "long", "filename": "a.mp4", "size_bytes": 10, "rights_confirmed": True, "terms_version": TERMS}
        r = self.h.c.post("/v1/jobs", json=body, headers=self.hd)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.ld.jobs, {})

    def test_scopes(self):
        ro = self.h.bearer(self.h.make_key(scopes=("read",)))
        r = self.h.c.post("/v1/jobs", json={}, headers=dict(ro, **{"Idempotency-Key": idem()}))
        self.assertEqual(r.json()["error"]["code"], "scope_required")
        self.assertEqual(self.get("/jobs", headers=ro).status_code, 200)

    def test_quote_for_clone_merge_is_invalid_and_unknown_job_404(self):
        jid = self.create().json()["id"]
        self.assertEqual(self.post("/jobs/%s/quotes" % jid, {"step": "merge"}).status_code, 400)
        self.assertEqual(self.post("/jobs/%s/quotes" % jid, {"step": "clone", "speaker_ids": ["a"]}).status_code, 400)
        self.assertEqual(self.post("/jobs/not-a-uuid/quotes", {"step": "estimate"}).status_code, 404)

    def test_wrong_state(self):
        jid = self.create().json()["id"]
        self.assertEqual(self.post("/jobs/%s/quotes" % jid, {"step": "estimate"}).json()["error"]["code"], "upload_incomplete")
        self.assertEqual(self.post("/jobs/%s/quotes" % jid, {"step": "analysis"}).json()["error"]["code"], "job_not_ready")
        self.assertEqual(self.post("/jobs/%s/quotes" % jid, {"step": "dub"}).json()["error"]["code"], "job_not_ready")
        self.assertEqual(self.get("/jobs/%s/estimate" % jid).json()["error"]["code"], "job_not_ready")
        self.assertEqual(self.get("/jobs/%s/results/mixed" % jid).json()["error"]["code"], "job_not_ready")

    def test_chunk_validation(self):
        jid = self.create().json()["id"]
        hdr = dict(self.hd, **{"Idempotency-Key": idem()})
        put = lambda i, n, **h: self.h.c.put("/v1/jobs/%s/upload/chunks/%d" % (jid, i), content=b"x" * n, headers=dict(hdr, **h))
        self.assertEqual(put(0, 999).status_code, 400)                                  # wrong length
        self.assertEqual(put(9, 1000).status_code, 400)                                 # outside the upload
        r = put(0, CHUNK + 1)
        self.assertEqual(r.json()["error"]["code"], "upload_too_large")
        self.assertEqual(self.ld.calls.count("chunk"), 0)
        self.assertEqual(put(0, 1000).status_code, 200)

    def test_delete(self):
        jid = self.create().json()["id"]
        r = self.h.c.delete("/v1/jobs/%s" % jid, headers=dict(self.hd, **{"Idempotency-Key": idem()}))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["media_present"])
        self.assertEqual(self.get("/jobs/%s" % jid).status_code, 404)

    def test_delete_while_processing_refused(self):
        jid = self.job_to_estimate()
        self.ld.jobs[jid]["status"] = "analyzing"
        r = self.h.c.delete("/v1/jobs/%s" % jid, headers=dict(self.hd, **{"Idempotency-Key": idem()}))
        self.assertEqual(r.json()["error"]["code"], "job_not_ready")


class Ownership(Base):
    def test_other_account_and_website_jobs_are_404(self):
        jid = self.create().json()["id"]
        other = self.h.bearer(self.h.make_key(uid=OTHER, scopes=("read", "dub", "account")))
        self.assertEqual(self.get("/jobs/%s" % jid, headers=other).status_code, 404)
        self.assertEqual(self.get("/jobs", headers=other).json()["jobs"], [])
        r = self.h.c.post("/v1/jobs/%s/quotes" % jid, json={"step": "estimate"}, headers=dict(other, **{"Idempotency-Key": idem()}))
        self.assertEqual(r.status_code, 404)
        web = "11111111-aaaa-4aaa-8aaa-111111111111"
        self.ld.jobs[web] = {"id": web, "uid": OWNER, "status": "estimated"}
        self.assertEqual(self.get("/jobs/%s" % web).status_code, 404)

    def test_quote_of_another_job_or_key_is_not_found(self):
        a = self.job_to_estimate()
        b = self.create().json()["id"]
        q = self.quote(a, "analysis")
        r = self.pay(b, q)
        self.assertEqual(r.json()["error"]["code"], "not_found")
        other = self.h.bearer(self.h.make_key(scopes=("read", "dub", "account")))
        self.assertEqual(self.pay(a, q, headers=other).json()["error"]["code"], "not_found")
        self.assertEqual(self.ld.calls.count("accept"), 0)


class Idempotency(Base):
    def test_replay_returns_same_answer_without_second_call(self):
        jid = self.create().json()["id"]
        self.upload(jid)
        q = self.quote(jid, "estimate")
        k = idem()
        a = self.pay(jid, q, k=k)
        b = self.pay(jid, q, k=k)
        self.assertEqual(a.status_code, 202)
        self.assertEqual(b.status_code, 202)
        self.assertEqual(b.headers.get("Idempotency-Replayed"), "true")
        self.assertEqual(a.json(), b.json())
        self.assertEqual(self.ld.calls.count("finish"), 1)
        self.assertEqual(self.ld.credits, 997)

    def test_same_key_different_body_conflicts(self):
        jid = self.create().json()["id"]
        self.upload(jid)
        q = self.quote(jid, "estimate")
        k = idem()
        self.assertEqual(self.pay(jid, q, k=k).status_code, 202)
        r = self.pay(jid, q, over={"max_credits": 99}, k=k)
        self.assertEqual(r.json()["error"]["code"], "idempotency_conflict")

    def test_create_replay_makes_one_job(self):
        body = {"kind": "long", "filename": "a.mp4", "size_bytes": 10, "rights_confirmed": True, "terms_version": TERMS}
        k = idem()
        a = self.post("/jobs", body, k=k)
        b = self.post("/jobs", body, k=k)
        self.assertEqual(a.json()["id"], b.json()["id"])
        self.assertEqual(len(self.ld.jobs), 1)

    def test_failed_validation_does_not_burn_the_key(self):
        k = idem()
        bad = self.post("/jobs", {"kind": "long"}, k=k)
        self.assertEqual(bad.status_code, 400)
        body = {"kind": "long", "filename": "a.mp4", "size_bytes": 10, "rights_confirmed": True, "terms_version": TERMS}
        self.assertEqual(self.post("/jobs", body, k=k).status_code, 201)


class Ceiling(Base):
    def test_live_price_above_ceiling_is_max_credits_exceeded_and_free(self):
        jid = self.job_to_editing()
        q = self.quote(jid, "dub")
        self.ld.cfg["clone_credits"] = 20                         # the owner raised a price after the quote
        r = self.pay(jid, q)
        self.assertEqual(r.json()["error"]["code"], "max_credits_exceeded")
        self.assertEqual(self.ld.credits, 977)
        self.assertEqual(self.ld.calls.count("confirm"), 0)
        self.assertEqual([o["step"] for o in self.h.db.t["api_operations"]], ["estimate", "analysis"])
        self.assertEqual([x["step"] for x in self.h.db.t["api_spend"]], ["estimate", "analysis"])

    def test_price_list_change_before_analysis_is_quote_changed(self):
        # the analysis amount is fixed by the stored estimate, so only the price version moves: refused, never charged
        jid = self.job_to_estimate()
        q = self.quote(jid, "analysis")
        self.ld.cfg["flat"] = 30
        self.assertEqual(self.pay(jid, q).json()["error"]["code"], "quote_changed")
        self.assertEqual(self.ld.credits, 997)
        self.assertEqual(self.ld.calls.count("accept"), 0)

    def test_price_down_is_quote_changed(self):
        jid = self.job_to_estimate()
        q = self.quote(jid, "analysis")
        self.ld.cfg["flat"] = 2
        r = self.pay(jid, q)
        self.assertEqual(r.json()["error"]["code"], "quote_changed")
        self.assertEqual(self.ld.calls.count("accept"), 0)

    def test_price_percent_change_is_quote_changed_even_with_same_total(self):
        jid = self.job_to_estimate()
        q = self.quote(jid, "analysis")
        self.ld.jobs[jid]["api"]["price_percent"] = 100
        self.ld.jobs[jid]["api"]["revision"] = 1             # text revised after the quote
        self.assertEqual(self.pay(jid, q).json()["error"]["code"], "quote_changed")

    def test_ceiling_below_live_price(self):
        jid = self.job_to_estimate()
        q = self.quote(jid, "analysis")
        r = self.pay(jid, q, over={"max_credits": q["quoted_credits"]})
        self.assertEqual(r.status_code, 202)

    def test_stated_quote_mismatch(self):
        jid = self.job_to_estimate()
        q = self.quote(jid, "analysis")
        r = self.pay(jid, q, over={"quoted_credits": q["quoted_credits"] - 1, "max_credits": q["quoted_credits"]})
        self.assertEqual(r.json()["error"]["code"], "quote_changed")

    def test_wrong_terms_version(self):
        jid = self.job_to_estimate()
        q = self.quote(jid, "analysis")
        self.assertEqual(self.pay(jid, q, over={"terms_version": "2000-01-01"}).json()["error"]["code"], "consent_required")
        self.assertEqual(self.ld.calls.count("accept"), 0)

    def test_expired_quote(self):
        jid = self.job_to_estimate()
        q = self.quote(jid, "analysis")
        self.h.clock[0] += 601
        self.assertEqual(self.pay(jid, q).json()["error"]["code"], "quote_changed")
        self.assertEqual(self.ld.calls.count("accept"), 0)
        q2 = self.quote(jid, "analysis")                          # a fresh quote works
        self.assertEqual(self.pay(jid, q2).status_code, 202)

    def test_dub_music_change_after_quote(self):
        jid = self.job_to_editing()
        q = self.quote(jid, "dub")
        self.ld.music_max = 40
        self.assertEqual(self.pay(jid, q).json()["error"]["code"], "max_credits_exceeded")
        self.ld.music_max = 10
        self.assertEqual(self.pay(jid, q).json()["error"]["code"], "quote_changed")
        self.assertEqual(self.ld.calls.count("confirm"), 0)

    def test_dub_without_music_quotes_no_additional(self):
        jid = self.job_to_editing()
        q = self.quote(jid, "dub", keep_music=False)
        self.assertEqual(q["additional_max_credits"], 0)
        self.assertEqual(self.pay(jid, q).status_code, 202)
        self.assertEqual(self.ld.credits, 1000 - 3 - 20 - 31)


class OneApproval(Base):
    def test_double_accept_with_different_keys_charges_once(self):
        jid = self.job_to_estimate()
        q = self.quote(jid, "analysis")
        a = self.pay(jid, q)
        b = self.pay(jid, q)
        self.assertEqual(a.status_code, 202)
        self.assertEqual(b.status_code, 202)               # continues the same approval, no second charge
        self.assertEqual(self.ld.credits, 1000 - 3 - 20)
        self.assertEqual([o["step"] for o in self.h.db.t["api_operations"]], ["estimate", "analysis"])
        self.assertEqual([x["amount"] for x in self.h.db.t["api_spend"]], [3, 20])

    def test_second_quote_for_an_approved_step_is_refused(self):
        jid = self.job_to_estimate()
        q1 = self.quote(jid, "analysis")
        q2 = self.quote(jid, "analysis")
        self.assertEqual(self.pay(jid, q1).status_code, 202)
        self.assertEqual(self.pay(jid, q2).json()["error"]["code"], "job_not_ready")
        self.assertEqual(self.ld.credits, 1000 - 3 - 20)

    def test_two_keys_same_job(self):
        jid = self.job_to_estimate()
        k2 = self.h.bearer(self.h.make_key(scopes=("read", "dub", "account")))
        q = self.quote(jid, "analysis")
        self.assertEqual(self.pay(jid, q).status_code, 202)
        q2 = self.post("/jobs/%s/quotes" % jid, {"step": "analysis"}, headers=k2)
        self.assertEqual(q2.json()["error"]["code"], "job_not_ready")     # the step is already approved: no new quote either
        self.assertEqual(self.ld.credits, 1000 - 3 - 20)


class DailyCap(Base):
    def test_cap_blocks_without_charge_and_next_day_resets(self):
        self.h.db.t["api_keys"][0]["daily_credit_cap"] = 30
        jid = self.job_to_estimate()                          # 3 reserved
        q = self.quote(jid, "analysis")                       # 20 -> 23 total
        self.assertEqual(self.pay(jid, q).status_code, 202)
        self.ld.jobs[jid].update(status="editing")
        q3 = self.quote(jid, "dub")                           # 51 > 30-23
        r = self.pay(jid, q3)
        self.assertEqual(r.json()["error"]["code"], "daily_cap_exceeded")
        self.assertEqual(self.ld.calls.count("confirm"), 0)
        self.assertEqual(self.ld.credits, 977)
        self.assertEqual(self.h.db.t["api_operations"].__len__(), 2)       # estimate + analysis only
        self.h.db.t["api_keys"][0]["daily_credit_cap"] = 500
        self.h.clock[0] += 86400
        q4 = self.quote(jid, "dub")
        self.assertEqual(self.pay(jid, q4).status_code, 202)

    def test_no_cap_means_no_spending(self):
        self.h.db.t["api_keys"][0]["daily_credit_cap"] = None
        jid = self.create().json()["id"]
        self.upload(jid)
        q = self.quote(jid, "estimate")
        self.assertEqual(self.pay(jid, q).json()["error"]["code"], "daily_cap_exceeded")
        self.assertEqual(self.ld.credits, 1000)

    def test_failed_payment_leaves_no_reservation(self):
        self.ld.credits = 2
        jid = self.create().json()["id"]
        self.upload(jid)
        q = self.quote(jid, "estimate")
        r = self.pay(jid, q)
        self.assertEqual(r.json()["error"]["code"], "insufficient_credits")
        self.assertEqual(self.h.db.t["api_spend"], [])
        self.assertEqual(self.h.db.t["api_operations"], [])
        self.ld.credits = 50                                  # the customer tops up: the same quote works again
        self.assertEqual(self.pay(jid, q).status_code, 202)
        self.assertEqual(self.ld.credits, 47)


class Uncertain(Base):
    def run_case(self, mode):
        jid = self.create().json()["id"]
        self.upload(jid)
        q = self.quote(jid, "estimate")
        self.ld.lose_reply = mode
        k = idem()
        r = self.pay(jid, q, k=k)
        self.assertEqual(r.json()["error"]["code"], "service_unavailable")
        again = self.pay(jid, q, k=k)                          # the same key can not start the step again
        self.assertEqual(again.json()["error"]["code"], "service_unavailable")
        r2 = self.pay(jid, q)                                  # a new key continues; the payment record prevents a second debit
        self.assertEqual(r2.status_code, 202, r2.text)
        self.assertEqual(self.ld.credits, 997)
        self.assertEqual(len(self.ld.ledger), 1)
        self.assertEqual(len(self.h.db.t["api_operations"]), 1)
        self.assertEqual(self.h.db.t["api_spend"][0]["amount"], 3)

    def test_reply_lost_before_charge(self):
        self.run_case("before")

    def test_reply_lost_after_charge(self):
        self.run_case("after")

    def test_exception_in_website_route_keeps_claim(self):
        jid = self.create().json()["id"]
        self.upload(jid)
        q = self.quote(jid, "estimate")
        orig = self.ld.finish

        async def boom(request, job_id):
            raise RuntimeError("boom")
        self.ld.finish = boom
        self.assertEqual(self.pay(jid, q).json()["error"]["code"], "service_unavailable")
        self.ld.finish = orig
        self.assertEqual(self.pay(jid, q).status_code, 202)
        self.assertEqual(self.ld.credits, 997)

    def test_analysis_lost_after_charge(self):
        jid = self.job_to_estimate()
        q = self.quote(jid, "analysis")
        self.ld.lose_reply = "after"
        self.assertEqual(self.pay(jid, q).json()["error"]["code"], "service_unavailable")
        self.assertEqual(self.pay(jid, q).status_code, 202)
        self.assertEqual(self.ld.credits, 977)


class Concurrency(Base):
    def test_second_active_job_is_refused(self):
        a = self.job_to_estimate()
        self.assertEqual(self.pay(a, self.quote(a, "analysis")).status_code, 202)
        self.ld.jobs[a]["status"] = "analyzing"
        b = self.job_to_estimate()
        q = self.quote(b, "analysis")
        self.assertEqual(self.pay(b, q).json()["error"]["code"], "concurrency_limited")
        self.assertEqual(self.h.db.t["api_spend"].__len__(), 3)
        self.ld.jobs[a]["status"] = "done"
        self.assertEqual(self.pay(b, q).status_code, 202)


class Classify(unittest.TestCase):
    def test_codes(self):
        c = api_dub.classify
        self.assertEqual(c(402, {"studio_required": True, "error": "x"}), "plan_required")
        self.assertEqual(c(429, {"error": "Too many"}), "concurrency_limited")
        self.assertEqual(c(409, {"error": "Not enough storage space"}), "storage_full")
        self.assertEqual(c(402, {"error": "Not enough credits."}), "insufficient_credits")
        self.assertEqual(c(500, {"error": "weird"}), "internal_error")
        self.assertEqual(c(503, {"capacity": True, "error": "busy"}), "service_unavailable")


if __name__ == "__main__":
    unittest.main()
