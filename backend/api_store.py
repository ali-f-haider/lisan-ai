"""Database access for the paid API steps: quotes, one-operation-per-step claims, daily spend reservations and
idempotency records. Every call goes through the `deps` functions of api_v1 (select / insert / update / delete), so
this module is tested with an in-memory fake and main.py stays the only place that knows about Supabase.

Tables (see api_phase2.sql): api_quotes, api_operations, api_spend, api_requests."""
import json
import threading

import api_idempotency
from api_core import Conflict

_KEY_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def key_lock(key_id):
    """One lock per API key inside this server process: the daily-cap check and the reservation are one step."""
    with _LOCKS_GUARD:
        return _KEY_LOCKS.setdefault(key_id, threading.RLock())


def _q(value):
    import urllib.parse
    return urllib.parse.quote(str(value), safe="")


class Store:
    def __init__(self, deps):
        self.d = deps

    # ---- quotes ----------------------------------------------------------------------------------
    def save_quote(self, quote, key_id, uid, expires_ts):
        self.d.insert("api_quotes", {"id": quote["id"], "job_id": quote["job_id"], "key_id": key_id, "uid": uid, "step": quote["step"],
                                     "quote": quote, "expires_ts": float(expires_ts)})

    def get_quote(self, quote_id, key_id, job_id):
        rows = self.d.select("api_quotes", "id=eq.%s&key_id=eq.%s&job_id=eq.%s&select=quote,expires_ts,step&limit=1" % (_q(quote_id), _q(key_id), _q(job_id)))
        return rows[0] if rows else None

    # ---- one operation per (job, step, revision, selection) ----------------------------------------
    def claim_operation(self, op):
        """True when this approval is new; False when this step of this job was already approved (retry or duplicate)."""
        try:
            self.d.insert("api_operations", op)
            return True
        except Conflict:
            return False

    def get_operation(self, job_id, step, revision, selection):
        rows = self.d.select("api_operations", "job_id=eq.%s&step=eq.%s&revision=eq.%d&selection=eq.%s&select=*&limit=1" % (_q(job_id), _q(step), int(revision), _q(selection)))
        return rows[0] if rows else None

    def release_operation(self, op_id):
        self.d.delete("api_operations", "id=eq.%s" % _q(op_id))

    # ---- daily spend reservations (gross: refunds never give a stolen key its allowance back) -------
    def spent_by_others(self, key_id, day, job_id, step):
        rows = self.d.select("api_spend", "key_id=eq.%s&day=eq.%s&select=job_id,step,amount&limit=5000" % (_q(key_id), _q(day)))
        return sum(int(r["amount"]) for r in rows if not (r.get("job_id") == job_id and r.get("step") == step))

    def reserve(self, key_id, day, job_id, step, amount):
        """Records (or refreshes) the reservation of one step. A retry of the same step is never counted twice."""
        try:
            self.d.insert("api_spend", {"key_id": key_id, "day": day, "job_id": job_id, "step": step, "amount": int(amount)})
        except Conflict:
            self.d.update("api_spend", "key_id=eq.%s&job_id=eq.%s&step=eq.%s" % (_q(key_id), _q(job_id), _q(step)), {"amount": int(amount)})

    def release_spend(self, key_id, job_id, step):
        self.d.delete("api_spend", "key_id=eq.%s&job_id=eq.%s&step=eq.%s" % (_q(key_id), _q(job_id), _q(step)))

    # ---- idempotency records ---------------------------------------------------------------------------
    def get_request(self, key_id, idem_key):
        rows = self.d.select("api_requests", "key_id=eq.%s&idem_key=eq.%s&select=*&limit=1" % (_q(key_id), _q(idem_key)))
        if not rows:
            return None
        r = rows[0]
        return {"key": r["idem_key"], "state": r["state"], "created_at": float(r["created_ts"]), "fingerprint": r["fingerprint"],
                "status": r.get("status"), "response": r.get("response")}

    def claim_request(self, key_id, idem_key, fingerprint, now):
        try:
            self.d.insert("api_requests", {"key_id": key_id, "idem_key": idem_key, "fingerprint": fingerprint, "state": "in_progress",
                                           "created_ts": float(now)})
            return True
        except Conflict:
            return False

    def complete_request(self, key_id, idem_key, status, response):
        self.d.update("api_requests", "key_id=eq.%s&idem_key=eq.%s" % (_q(key_id), _q(idem_key)),
                      {"state": "complete", "status": int(status), "response": response})

    def drop_request(self, key_id, idem_key):
        """Used only when it is PROVEN that nothing was started or charged, so the same key may be tried again."""
        self.d.delete("api_requests", "key_id=eq.%s&idem_key=eq.%s" % (_q(key_id), _q(idem_key)))

    def replace_expired_request(self, key_id, idem_key, old_created_ts):
        """A completed record older than 24 h no longer protects; remove it so the key can be used again."""
        self.d.delete("api_requests", "key_id=eq.%s&idem_key=eq.%s&created_ts=eq.%s" % (_q(key_id), _q(idem_key), repr(float(old_created_ts))))
