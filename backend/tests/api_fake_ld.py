"""A stand-in for the website's long-dub route code, with the same rules the real one has (state machine, one
payment per slot keyed by a stable operation id, the website's own error sentences). Not a test file."""
import math
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.responses import Response

import api_settings

UNCERTAIN = "Your payment could not be confirmed. Please retry."
CFG = {"fee": 3, "analysis_per_min": 2.0, "flat": 10, "clone_credits": 5, "merge_credits": 1, "chars_per_credit": 60,
       "speaker_check_flat": 0, "speaker_check_per_min": 0.0, "music_fill_credits": 10, "lipsync_per_sec": 40.0, "max_min": 60}
CHUNK = 1000


class FakeLD:
    def __init__(self, credits=1000, cfg=None):
        self.jobs, self.credits, self.cfg = {}, credits, dict(cfg or CFG)
        self.ledger = {}                       # operation id -> amount (one payment per operation id, like the real ledger)
        self.calls = []                        # every website-route call, in order
        self.lose_reply = None                 # "before" | "after": the next paid call answers "could not be confirmed"
        self.music_max = 20
        self.chars = 1200
        self.speakers = 2
        self.deleted = []

    # ---- reads -------------------------------------------------------------------------------------
    def load(self, job_id):
        return self.jobs.get(job_id)

    def jobs_for(self, uid):
        return [j for j in self.jobs.values() if j.get("uid") == uid]

    def pricing(self, job):
        pct = (job.get("api") or {}).get("price_percent")
        return api_settings.scale_pricing(self.cfg, pct) if pct and pct != 100 else dict(self.cfg)

    def result_files(self, job):
        return {"mixed": (3, ".mp4"), "dialogue": (2, ".m4a")} if job.get("status") == "done" else {}

    def file_response(self, job, kind):
        if kind == "background":
            return None
        return Response(content=b"abc", media_type="video/mp4")

    def tag(self, job_id, patch):
        job = self.jobs[job_id]
        job.setdefault("api", {}).update(patch)

    # ---- the website's routes ---------------------------------------------------------------------------
    def _charge(self, job, slot, amount):
        op = "%s:%s" % (job["id"], slot)
        if op not in self.ledger:
            if self.credits < amount:
                return False
            self.credits -= amount
            self.ledger[op] = amount
        job.setdefault("ops", {})[slot] = {"id": op, "amount": amount}
        job["paid"][slot] = amount
        return True

    def _maybe_lose(self, job, slot, amount):
        """Charges, then loses the reply (mode 'after'), or loses the reply without charging ('before')."""
        if self.lose_reply == "before":
            self.lose_reply = None
            return (503, {"error": UNCERTAIN})
        ok = self._charge(job, slot, amount)
        if not ok:
            return (402, {"error": "Not enough credits."})
        if self.lose_reply == "after":
            self.lose_reply = None
            return (503, {"error": UNCERTAIN})
        return None

    async def create(self, request, params):
        self.calls.append("create")
        if not str(params["filename"]).lower().endswith((".mp4", ".mp3")):
            return 400, {"error": "Unsupported file type."}
        jid = str(uuid.uuid4())
        total = int(math.ceil(params["size"] / float(CHUNK)))
        self.jobs[jid] = {"id": jid, "uid": request.state.api_principal["uid"], "filename": params["filename"], "size": params["size"],
                          "name": params.get("name") or "clip", "status": "uploading", "stage": "upload", "percent": 0, "received": [],
                          "total_chunks": total, "chunk_bytes": CHUNK, "duration": 0.0, "estimate": None,
                          "paid": {"fee": 0, "analysis": 0, "dub": 0}, "created": time.time() + len(self.jobs), "updated": time.time(),
                          "media_present": True}
        return 200, {"id": jid}

    async def chunk(self, request, job_id, index):
        self.calls.append("chunk")
        job = self.jobs[job_id]
        if index not in job["received"]:
            job["received"].append(index)
        return 200, {}

    async def finish(self, request, job_id):
        self.calls.append("finish")
        job = self.jobs[job_id]
        if job["status"] != "uploading":
            return 409, {"error": "This upload is already finished."}
        fee = int(self.pricing(job)["fee"])
        if self.credits < fee and not job["paid"]["fee"]:
            return 402, {"error": "Getting an estimate costs %d credits." % fee}
        lost = self._maybe_lose(job, "fee", fee) if not job["paid"]["fee"] else None
        if lost:
            if lost[0] == 503 and job["paid"]["fee"]:
                job.update(status="estimated", stage="estimate", percent=100, duration=300.0, estimate=self._estimate(job))
            return lost
        job.update(status="estimated", stage="estimate", percent=100, duration=300.0, estimate=self._estimate(job))
        return 200, {"id": job_id}

    def _estimate(self, job):
        c = self.pricing(job)
        analysis = int(math.ceil(5 * c["analysis_per_min"]))
        voice = int(math.ceil(self.chars / c["chars_per_credit"]))
        clones = c["clone_credits"] * self.speakers
        merge = c["merge_credits"]
        return {"fee": c["fee"], "analysis": analysis, "flat": c["flat"], "speaker_check": 0, "voice": voice, "clones": clones,
                "merge": merge, "total": c["fee"] + analysis + c["flat"] + voice + clones + merge}

    async def accept(self, request, job_id):
        self.calls.append("accept")
        job = self.jobs[job_id]
        if job["status"] != "estimated":
            return 409, {"error": "This job is not waiting for approval."}
        est = job["estimate"]
        need = est["analysis"] + est["flat"]
        if self.credits < est["total"] - job["paid"]["fee"]:
            return 402, {"error": "Not enough credits."}
        lost = self._maybe_lose(job, "analysis", need)
        if lost:
            if lost[0] == 503 and job["paid"]["analysis"]:
                job.update(status="accepted", stage="queued")
            return lost
        job.update(status="accepted", stage="queued", percent=0)
        return 200, {"id": job_id}

    def dub_numbers(self, job):
        c = self.pricing(job)
        voice = int(math.ceil(self.chars / c["chars_per_credit"]))
        clones = c["clone_credits"] * self.speakers
        merge = c["merge_credits"]
        return {"voice": voice, "clones": clones, "merge": merge, "due": voice + clones + merge,
                "already_paid": job["paid"]["fee"] + job["paid"]["analysis"]}

    async def preview(self, request, job_id):
        self.calls.append("preview")
        job = self.jobs[job_id]
        if job["status"] != "editing":
            return 409, {"error": "This job is not open for editing."}
        p = self.dub_numbers(job)
        p["music"] = {"repairs": 2, "max_credits": self.music_max, "kept": True}
        return 200, p

    async def confirm(self, request, job_id, expected_due, tracks, keep_music, music_budget):
        self.calls.append("confirm")
        job = self.jobs[job_id]
        if job["status"] != "editing":
            return 409, {"error": "This job is not waiting for confirmation."}
        p = self.dub_numbers(job)
        if expected_due != p["due"]:
            return 409, {"error": "The price changed because the text was edited."}
        if music_budget != (self.music_max if keep_music else 0):
            return 409, {"error": "The music repair price changed."}
        if self.credits < p["due"] + music_budget:
            return 402, {"error": "Not enough credits."}
        lost = self._maybe_lose(job, "dub", p["due"])
        if lost:
            if lost[0] == 503 and job["paid"]["dub"]:
                job.update(status="confirmed", stage="queued")
            return lost
        job.update(status="confirmed", stage="queued", percent=0)
        return 200, {"id": job_id}

    async def delete(self, request, job_id):
        self.calls.append("delete")
        job = self.jobs[job_id]
        if job["status"] in ("accepted", "analyzing", "confirmed", "dubbing"):
            return 409, {"error": "This job is being processed right now and can't be deleted yet."}
        self.jobs[job_id] = {"id": job_id, "uid": job["uid"], "status": "account_cleanup"}
        self.deleted.append(job_id)
        return 200, {"ok": True}
