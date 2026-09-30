#!/usr/bin/env python3
"""
Lisan AI -- Supabase access audit.  Run this ON YOUR COMPUTER (not on Railway).

What it does: it behaves like an attacker who only has what every visitor's
browser already has -- your PUBLIC "anon" key -- plus (optionally) two ordinary
test accounts, and tries to read or change things it must not be able to:
  * read any table (profiles, credits, sessions, admin tokens, ...)
  * write to any table
  * call the credit functions (add_credits / deduct_credits / ...) directly
  * read or change ANOTHER user's profile or credits

Every line ends in PASS or FAIL. Any FAIL is a real hole: run
supabase_lockdown.sql in the Supabase SQL editor, then run this again until
there are no FAILs.

It never reads your service key and never sends anything anywhere except your
own Supabase project. Uses only the Python standard library.

How to run (Windows PowerShell, in the backend folder):
    python supabase_rls_audit.py

It reads SUPABASE_URL and SUPABASE_ANON_KEY from .env (or asks for them).
For the two-user checks it asks for the e-mail + password of TWO THROWAWAY
test accounts (sign up twice on your own site with throwaway e-mails -- never
use your real account). Press Enter at the prompt to skip the two-user checks.
Add --no-writes to skip the one test that tries to change a test account's
credits (it only ever touches the throwaway accounts).
"""
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ZERO_UUID = "00000000-0000-0000-0000-000000000000"
KNOWN_TABLES = [
    "profiles", "credit_spends", "credit_orders", "credit_audit", "user_voices",
    "subscription_invoices", "pricing_config", "app_sessions", "admin_sessions",
    "long_dub_events", "lipsync_runs", "expiry_notices", "consent_records",
]
# tables whose rows hold session / admin tokens: ANY row visible to a visitor is a leak
TOKEN_TABLES = {"app_sessions", "admin_sessions"}
CREDIT_FUNCTIONS = {
    "add_credits": {"uid": ZERO_UUID, "amount": 0},
    "deduct_credits": {"uid": ZERO_UUID, "amount": 0},
    "deduct_subscription_credits": {"uid": ZERO_UUID, "amount": 0},
}

results = []


def record(ok, name, detail=""):
    results.append((ok, name, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (("   -> " + detail) if (detail and not ok) else ""))


def note(name, detail):
    print("NOTE  " + name + "   -> " + detail)


def load_env():
    env = {}
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, ".env"), ".env"):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8").read().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


class Api:
    def __init__(self, base, anon):
        self.base = base.rstrip("/")
        self.anon = anon

    def call(self, method, path, token=None, body=None, prefer=None, timeout=20):
        headers = {"apikey": self.anon, "Authorization": "Bearer " + (token or self.anon)}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            raw = e.read()
            status = e.code
        except Exception as e:
            return 0, {"message": str(e)}
        try:
            parsed = json.loads(raw) if raw else None
        except Exception:
            parsed = {"raw": raw[:200].decode("utf-8", "replace")}
        return status, parsed


def denied(status, body):
    """True when Postgres/PostgREST refused on permissions."""
    code = str((body or {}).get("code", "")) if isinstance(body, dict) else ""
    return status in (401, 403) or code in ("42501", "PGRST301", "PGRST302")


def discover(api):
    """Ask PostgREST which tables/functions a plain visitor can even see."""
    tables, rpcs = set(KNOWN_TABLES), set(CREDIT_FUNCTIONS)
    st, spec = api.call("GET", "/rest/v1/")
    if st == 200 and isinstance(spec, dict):
        for name in (spec.get("definitions") or {}):
            tables.add(name)
        for path in (spec.get("paths") or {}):
            if path.startswith("/rpc/"):
                rpcs.add(path[5:])
            elif path != "/" and not path.startswith("/rpc"):
                tables.add(path.strip("/"))
    else:
        note("schema listing", "visitor cannot list the schema (status %s) - good; testing the known names only" % st)
    return sorted(tables), sorted(rpcs)


def rows_of(body):
    return body if isinstance(body, list) else []


def audit_anonymous(api, tables, rpcs):
    print("\n== 1. A stranger with only the public key ==")
    for t in tables:
        st, body = api.call("GET", "/rest/v1/%s?select=*&limit=3" % urllib.parse.quote(t))
        if st == 200 and rows_of(body):
            record(False, "read %s" % t, "a stranger can READ %d row(s)" % len(body))
        elif st == 200:
            record(True, "read %s" % t)  # empty: RLS hides everything (or table is empty)
        else:
            record(True, "read %s" % t)
        st, body = api.call("POST", "/rest/v1/%s" % urllib.parse.quote(t), body={}, prefer="return=minimal")
        if st in (200, 201, 204):
            record(False, "write %s" % t, "a stranger can INSERT")
        elif denied(st, body):
            record(True, "write %s" % t)
        else:
            code = (body or {}).get("code") if isinstance(body, dict) else ""
            # a constraint error (23502 etc.) means the insert got PAST the permission check
            record(False, "write %s" % t, "insert reached the table (status %s, code %s) - INSERT privilege is granted" % (st, code))
    for fn in rpcs:
        args = CREDIT_FUNCTIONS.get(fn, {})
        st, body = api.call("POST", "/rest/v1/rpc/%s" % urllib.parse.quote(fn), body=args)
        if st in (200, 204):
            record(False, "run function %s()" % fn, "a stranger can RUN it (status %s)" % st)
        elif denied(st, body):
            record(True, "run function %s()" % fn)
        elif st == 404 or (isinstance(body, dict) and str(body.get("code", "")) == "PGRST202"):
            record(True, "run function %s()" % fn)
        else:
            record(False, "run function %s()" % fn, "unclear answer (status %s %s) - treat as exposed" % (st, body))


def sign_in(api, email, password):
    st, body = api.call("POST", "/auth/v1/token?grant_type=password", body={"email": email, "password": password})
    if st == 200 and isinstance(body, dict) and body.get("access_token"):
        return body["access_token"], (body.get("user") or {}).get("id")
    return None, None


def audit_users(api, tables, rpcs, a, b, writes):
    print("\n== 2. A logged-in test user (A) versus another user (B) ==")
    tok_a, uid_a = a
    tok_b, uid_b = b
    for t in tables:
        st, body = api.call("GET", "/rest/v1/%s?select=*&limit=200" % urllib.parse.quote(t), token=tok_a)
        rows = rows_of(body)
        if t in TOKEN_TABLES and rows:
            record(False, "user A reads %s" % t, "%d session/admin token row(s) visible to an ordinary user" % len(rows))
            continue
        foreign = [r for r in rows if isinstance(r, dict) and (
            (r.get("uid") not in (None, uid_a) and "uid" in r) or (r.get("id") not in (None, uid_a) and t == "profiles"))]
        if foreign:
            record(False, "user A reads %s" % t, "sees %d row(s) belonging to OTHER users" % len(foreign))
        else:
            record(True, "user A reads %s (only own rows, or none)" % t)
    # direct read of B's profile
    st, body = api.call("GET", "/rest/v1/profiles?id=eq.%s&select=*" % uid_b, token=tok_a)
    record(not rows_of(body), "user A reads user B's profile", "returned B's profile")
    # credit functions as a normal user
    for fn in ("add_credits", "deduct_credits", "deduct_subscription_credits"):
        if fn not in rpcs:
            continue
        for label, target in (("own", uid_a), ("B's", uid_b)):
            st, body = api.call("POST", "/rest/v1/rpc/%s" % fn, token=tok_a, body={"uid": target, "amount": 0})
            record(st not in (200, 204), "user A runs %s() on %s account" % (fn, label),
                   "a normal user can run the credit function (status %s)" % st)
    if not writes:
        note("write tests", "skipped (--no-writes)")
        return
    # privilege probe that changes nothing (filter can never match)
    st, body = api.call("PATCH", "/rest/v1/profiles?id=eq.%s&credits=eq.-999999" % uid_a, token=tok_a,
                        body={"credits": 1}, prefer="return=representation")
    if denied(st, body):
        record(True, "user A may change credits on profiles (no privilege at all)")
    else:
        # privilege exists: prove whether it can really be abused
        st, body = api.call("PATCH", "/rest/v1/profiles?id=eq.%s" % uid_b, token=tok_a,
                            body={"credits": 987654}, prefer="return=representation")
        record(not rows_of(body), "user A changes user B's credits", "A SUCCEEDED in editing B's profile")
        st, body = api.call("PATCH", "/rest/v1/profiles?id=eq.%s" % uid_a, token=tok_a,
                            body={"credits": 987654}, prefer="return=representation")
        record(not rows_of(body), "user A gives themself credits",
               "A set its own credits to 987654 - reset that test account's credits in Supabase")


def main():
    writes = "--no-writes" not in sys.argv
    env = load_env()
    url = env.get("SUPABASE_URL") or input("SUPABASE_URL (https://xxxx.supabase.co): ").strip()
    anon = env.get("SUPABASE_ANON_KEY") or input("SUPABASE_ANON_KEY (the public 'anon' key): ").strip()
    if not url or not anon:
        print("Need both SUPABASE_URL and SUPABASE_ANON_KEY.")
        return 2
    api = Api(url, anon)
    tables, rpcs = discover(api)
    print("Testing %d table(s) and %d function(s) as a stranger." % (len(tables), len(rpcs)))
    audit_anonymous(api, tables, rpcs)
    print("\nFor the two-user checks, enter two THROWAWAY test accounts (Enter to skip).")
    ea = input("Test user A e-mail: ").strip()
    if ea:
        pa = getpass.getpass("Test user A password: ")
        eb = input("Test user B e-mail: ").strip()
        pb = getpass.getpass("Test user B password: ") if eb else ""
        a = sign_in(api, ea, pa)
        b = sign_in(api, eb, pb) if eb else (None, None)
        if a[0] and b[0]:
            audit_users(api, tables, rpcs, a, b, writes)
        else:
            print("Could not sign in both test users (wrong password, or e-mail not confirmed). Skipping part 2.")
    fails = [r for r in results if not r[0]]
    print("\n==== %d checks, %d FAILED ====" % (len(results), len(fails)))
    for ok, name, detail in fails:
        print("  FAIL  %s   -> %s" % (name, detail))
    if fails:
        print("\nRun supabase_lockdown.sql in the Supabase SQL editor, then run this again.")
    else:
        print("\nNo holes found by these checks.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
