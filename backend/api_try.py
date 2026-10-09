"""Try the Lisan AI public API by hand, step by step (long dubbing). Needs only Python 3, nothing to install.

Setup (Windows Command Prompt), once per window:
    set LISAN_URL=https://YOUR-SITE-ADDRESS
    set LISAN_KEY=lsn_live_...your key...

Commands:
    python api_try.py balance
    python api_try.py start  "C:\\path\\to\\clip.mp4"     upload, get the estimate, ask before each charge
    python api_try.py status JOB_ID
    python api_try.py dub    JOB_ID                      after you reviewed the text on the website
    python api_try.py get    JOB_ID                      download the finished files into this folder
    python api_try.py list

Every step that costs credits shows you the price and waits for you to type yes. Nothing is charged before that.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

TERMS = "2026-10-1"          # version of the API terms the server expects (shown in Account > API keys)
BASE = os.environ.get("LISAN_URL", "").rstrip("/")
KEY = os.environ.get("LISAN_KEY", "")


def die(msg):
    print(msg)
    sys.exit(1)


def call(method, path, body=None, raw=None, headers=None, expect_json=True):
    h = {"Authorization": "Bearer " + KEY, "Accept": "application/json", "User-Agent": "lisan-api-try/1"}
    data = None
    if method in ("POST", "PUT", "DELETE"):
        h["Idempotency-Key"] = "try-" + uuid.uuid4().hex
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    if raw is not None:
        data = raw
        h["Content-Type"] = "application/octet-stream"
    h.update(headers or {})
    req = urllib.request.Request(BASE + "/v1" + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            payload = r.read()
            return r.status, (json.loads(payload) if expect_json and payload else payload)
    except urllib.error.HTTPError as ex:
        payload = ex.read()
        try:
            return ex.code, json.loads(payload)
        except Exception:
            return ex.code, {"error": {"code": "unreadable", "message": payload[:200].decode("utf-8", "replace")}}
    except Exception as ex:
        die("Could not reach the server (%s). Check LISAN_URL and your internet." % type(ex).__name__)


def need(status, data, what):
    if 200 <= status < 300:
        return data
    err = (data or {}).get("error", {}) if isinstance(data, dict) else {}
    die("%s failed (%s): %s - %s" % (what, status, err.get("code", "?"), err.get("message", "")))


def yes(question):
    return input(question + " Type yes to continue: ").strip().lower() in ("yes", "y")


def pay(job_id, step, extra=None):
    """Quote a step, show the price, ask, then approve with the price as the ceiling."""
    q = need(*call("POST", "/jobs/%s/quotes" % job_id, dict({"step": step}, **(extra or {}))), "Quote for " + step)
    print("\n--- %s ---" % step.upper())
    print(q["notice"])
    for item in q["breakdown"]:
        print("   %-18s %5d credits" % (item["code"], item["credits"]))
    print("   You will be charged: %d credits (%.2f USD). Of that, up to %d is the optional music repair, charged only if it happens." % (
        q["quoted_credits"], q["quoted_credits"] / 100.0, q["additional_max_credits"]))
    print("   This quote is valid until", q["expires_at"])
    if q["quoted_credits"] == 0:
        print("   Nothing to pay for this step.")
    if not yes("Approve this step?"):
        die("Stopped. Nothing was charged for this step.")
    body = {"quote_id": q["id"], "quoted_credits": q["quoted_credits"], "max_credits": q["quoted_credits"], "terms_version": q["terms_version"]}
    path = "/jobs/%s/upload/finish" % job_id if step == "estimate" else "/jobs/%s/accept" % job_id
    job = need(*call("POST", path, body), "Approval of " + step)
    op = job.get("current_operation") or {}
    print("   Approved. Status: %s / %s. Credits taken so far for this step: %s" % (job["status"], job["stage"], op.get("credits_debited")))
    return job


def show_job(j):
    print("Job %s: %s (%s), %s%%, revision %s" % (j["id"], j["status"], j["stage"], j["percent"], j["revision"]))
    if j.get("duration_seconds"):
        print("   length: %.0f seconds" % j["duration_seconds"])
    op = j.get("current_operation")
    if op:
        print("   last paid step: %s, %s, taken %s, returned %s credits" % (op["step"], op["state"], op["credits_debited"], op["credits_refunded"]))
    if j.get("error"):
        print("   error:", j["error"].get("code"), "-", j["error"].get("message"))
    print("   result ready:", j["result_available"])


def cmd_balance():
    print(need(*call("GET", "/account/balance"), "Balance"))


def cmd_list():
    for j in need(*call("GET", "/jobs?limit=20"), "List")["jobs"]:
        show_job(j)


def cmd_status(job_id):
    show_job(need(*call("GET", "/jobs/" + job_id), "Status"))


def cmd_start(path):
    if not os.path.isfile(path):
        die("File not found: " + path)
    size = os.path.getsize(path)
    print("File: %s (%.1f MB). It must be at least 20 seconds long." % (os.path.basename(path), size / 1048576.0))
    if not yes("Do you confirm you have the right to dub this file, and accept the API terms shown in your account?"):
        die("Stopped.")
    job = need(*call("POST", "/jobs", {"kind": "long", "filename": os.path.basename(path), "size_bytes": size,
                                        "rights_confirmed": True, "terms_version": TERMS}), "Create job")
    jid = job["id"]
    print("Job created:", jid, "(nothing charged yet)")
    up = need(*call("GET", "/jobs/%s/upload" % jid), "Upload info")
    with open(path, "rb") as f:
        for i in range(up["total_chunks"]):
            chunk = f.read(up["chunk_bytes"])
            need(*call("PUT", "/jobs/%s/upload/chunks/%d" % (jid, i), raw=chunk), "Upload part %d" % i)
            print("   uploaded part %d of %d" % (i + 1, up["total_chunks"]))
    pay(jid, "estimate")
    est = need(*call("GET", "/jobs/%s/estimate" % jid), "Estimate")
    print("\nEstimate for the WHOLE job: about %d credits (%.2f USD); already paid %d. %s" % (
        est["estimated_total_credits"], est["estimated_total_credits"] / 100.0, est["already_paid_credits"], est["notice"]))
    pay(jid, "analysis")
    print("\nThe server now analyses the file (listening, speakers, translation). Check progress with:")
    print("    python api_try.py status " + jid)
    print("When the status says 'editing', open the project on the website, review and correct the text, then run:")
    print("    python api_try.py dub " + jid)


def cmd_dub(job_id):
    j = need(*call("GET", "/jobs/" + job_id), "Status")
    if j["status"] != "editing":
        die("The job is '%s', not 'editing'. Wait until the analysis is finished (python api_try.py status %s)." % (j["status"], job_id))
    keep = yes("Keep the original music under the speech (a repair fee may apply, shown next)?")
    tracks = yes("Also keep the dubbed voices and the music as separate files?")
    pay(job_id, "dub", {"keep_music": keep, "tracks": tracks})
    print("\nDubbing started. Check with: python api_try.py status " + job_id)


def cmd_get(job_id):
    res = need(*call("GET", "/jobs/%s/results" % job_id), "Results")["results"]
    if not res:
        die("No result yet.")
    for r in res:
        status, data = call("GET", "/jobs/%s/results/%s" % (job_id, r["kind"]), expect_json=False)
        if status != 200:
            print("Could not download", r["kind"], status)
            continue
        name = "%s_%s%s" % (job_id[:8], r["kind"], ".mp4" if r["media_type"] == "video/mp4" else ".m4a" if r["media_type"] == "audio/mp4" else ".mp3")
        with open(name, "wb") as f:
            f.write(data)
        print("Saved", name, "(%d bytes)" % len(data))


def main():
    if not BASE.startswith("http") or not KEY.startswith("lsn_live_"):
        die("Set LISAN_URL and LISAN_KEY first (see the top of this file).")
    a = sys.argv[1:]
    if not a:
        die(__doc__)
    cmd = a[0]
    if cmd == "balance":
        cmd_balance()
    elif cmd == "list":
        cmd_list()
    elif cmd in ("status", "dub", "get", "start") and len(a) == 2:
        {"status": cmd_status, "dub": cmd_dub, "get": cmd_get, "start": cmd_start}[cmd](a[1])
    else:
        die(__doc__)


if __name__ == "__main__":
    main()
