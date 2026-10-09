"""Shared fakes for the public API tests: an in-memory database that enforces unique keys like the real one, and a
test client harness. Not a test file."""
import sys
import urllib.parse
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api_keys
import api_settings
import api_v1
from api_core import Conflict

PEPPER = b"p" * 40
OWNER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"

# the unique constraints api_phase1.sql / api_phase2.sql declare
UNIQUE = {
    "api_keys": [("key_hash",)],
    "api_owner_acceptances": [("uid", "terms_version")],
    "api_quotes": [("id",)],
    "api_operations": [("quote_id",), ("job_id", "step", "revision", "selection")],
    "api_spend": [("key_id", "job_id", "step")],
    "api_requests": [("key_id", "idem_key")],
}


class FakeDB:
    def __init__(self):
        self.t = {name: [] for name in UNIQUE}
        self.fail = False
        self.calls = []

    def _match(self, row, query):
        for part in query.split("&"):
            if "=eq." in part:
                col, val = part.split("=eq.", 1)
                if str(row.get(col)) != urllib.parse.unquote(val):
                    return False
        return True

    def select(self, table, query):
        if self.fail:
            raise RuntimeError("db down")
        return [dict(r) for r in self.t[table] if self._match(r, query)]

    def insert(self, table, row):
        if self.fail:
            raise RuntimeError("db down")
        r = dict(row)
        for cols in UNIQUE[table]:
            if all(c in r for c in cols) and any(all(str(x.get(c)) == str(r[c]) for c in cols) for x in self.t[table]):
                raise Conflict()
        r.setdefault("id", str(uuid.uuid4()))
        r.setdefault("created_at", "2026-10-01T00:00:00+00:00")
        self.t[table].append(r)
        self.calls.append(("insert", table))
        return dict(r)

    def update(self, table, query, patch):
        if self.fail:
            raise RuntimeError("db down")
        for r in self.t[table]:
            if self._match(r, query):
                r.update(patch)

    def delete(self, table, query):
        if self.fail:
            raise RuntimeError("db down")
        self.t[table] = [r for r in self.t[table] if not self._match(r, query)]
        self.calls.append(("delete", table))


class Harness:
    def __init__(self, ld=None, **over):
        self.db = FakeDB()
        self.settings = api_settings.normalize({"enabled": True, "requests_per_minute": 60, "burst": 3})
        self.settings.update(over)
        self.clock = [1_800_000_000.0]
        self.balance = {"credits": 1234, "subscription_credits": 234, "permanent_credits": 1000}
        self.eligible = True
        self.owner = OWNER
        self.pepper = (1, PEPPER)
        d = api_v1.Deps(select=self.db.select, insert=self.db.insert, update=self.db.update,
                        settings=lambda: self.settings, balance=lambda uid: self.balance,
                        eligible=lambda uid: self.eligible, owner_uid=lambda req: self.owner,
                        pepper=lambda: self.pepper, now=lambda: self.clock[0], delete=self.db.delete, ld=ld)
        self.deps = d
        self.api, self.ownr, self.limiter = api_v1.build(d)
        app = FastAPI()
        app.include_router(self.api)
        app.include_router(self.ownr)
        app.add_exception_handler(api_v1.ApiError, api_v1.handle_api_error)
        self.c = TestClient(app, raise_server_exceptions=False)

    def make_key(self, uid=OWNER, scopes=("read", "account"), state="active", expires=None, cap=500):
        key = api_keys.generate_key()
        self.db.insert("api_keys", {"uid": uid, "name": "t", "prefix": api_keys.dashboard_prefix(key), "pepper_version": 1,
                                    "key_hash": api_keys.storage_hash(key, PEPPER), "scopes": list(scopes), "state": state,
                                    "expires_at": expires, "daily_credit_cap": cap})
        return key

    @staticmethod
    def bearer(key):
        return {"Authorization": "Bearer " + key}
