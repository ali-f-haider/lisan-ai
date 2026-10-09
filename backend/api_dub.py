"""Public API v1: long-dub jobs, upload, quotes and approvals, results (phase 2a).

Every website rule is applied by the website's own route code: the handlers here call it through `deps.ld`
(see main.py), so the plan gate, the billing pause, storage and disk checks, ownership, the charge and refund
paths and the price calculators are the same ones the website uses. This module adds only what an API needs on
top: the quote/approval ceiling, the key's daily credit cap, one approval per step, and idempotent retries.

Money rules enforced here (each has a test):
  * nothing is charged without a quote the caller approved with a ceiling (`max_credits`) covering the live price;
  * a live price above the ceiling is max_credits_exceeded, any other change is quote_changed -- both before any debit;
  * a step is approved at most once per job (unique claim), however many keys, quotes or retries are used;
  * the key's daily cap counts the whole maximum of each approved step, atomically with the claim;
  * a retry with the same Idempotency-Key replays the stored answer and never repeats the step;
  * uncertain payment outcomes are never released for a second attempt by this layer: the website's own
    stable per-step payment record decides, so a retry cannot charge twice.
"""
import json
import uuid
from datetime import datetime, timezone

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import api_errors
import api_idempotency
import api_jobs
import api_money
import api_schema
import api_settings
from api_core import ApiError
from api_store import Store, key_lock

_UUID = __import__("re").compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_ACTIVE = ("accepted", "analyzing", "confirmed", "dubbing", "payment_pending")
_ORDER = ["uploading", "estimated", "accepted", "analyzing", "editing", "confirmed", "dubbing", "done"]
_BEFORE = {"estimate": "uploading", "analysis": "estimated", "dub": "editing"}
_UNCERTAIN = "Your payment could not be confirmed. Please retry."
_MAX_JSON = 65536


class Uncertain(Exception):
    """A paid step may or may not have been charged; this layer must not release the claim."""


def classify(status, data):
    """The closed error code for an answer of the website's route code (never the website's own text)."""
    if isinstance(data, dict) and data.get("studio_required"):
        return "plan_required"
    msg = data.get("error") if isinstance(data, dict) else None
    low = str(msg or "").lower()
    if status == 429:
        return "concurrency_limited"
    if status == 409 and ("storage" in low or "space" in low):
        return "storage_full"
    if status == 503 and isinstance(data, dict) and data.get("capacity"):
        return "service_unavailable"
    return api_errors.from_app_error(msg, status)


def _strict_json(raw):
    def pairs(items):
        d = {}
        for k, v in items:
            if k in d:
                raise ValueError("duplicate key")
            d[k] = v
        return d
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)


async def read_json(request):
    raw = await request.body()
    if len(raw) > _MAX_JSON:
        raise ApiError("invalid_request")
    try:
        body = _strict_json(raw)
    except Exception:
        raise ApiError("invalid_request")
    if not isinstance(body, dict):
        raise ApiError("invalid_request")
    return body


class _InThread:
    """Runs every database call of a Store in a worker thread, so a slow database never blocks the server's event loop."""
    def __init__(self, store):
        self._s = store

    def __getattr__(self, name):
        fn = getattr(self._s, name)

        async def call(*a, **kw):
            return await run_in_threadpool(lambda: fn(*a, **kw))
        return call


def register(api, deps, principal, ok):
    ld = deps.ld
    store = Store(deps)
    astore = _InThread(store)

    # ---- helpers ---------------------------------------------------------------------------------------
    def job_of(p, job_id):
        if not isinstance(job_id, str) or not _UUID.fullmatch(job_id):
            raise ApiError("not_found")
        job = ld.load(job_id)
        if api_jobs.is_gone(job) or job.get("uid") != p["uid"] or not isinstance(job.get("api"), dict):
            raise ApiError("not_found")          # another account's job, a website-only project and a missing job look the same
        return job

    def result_available(job):
        return bool(ld.result_files(job).get("mixed")) if job.get("status") == "done" else False

    def view(request, job):
        return api_jobs.job_view(job, request.state.api_request_id, result_available(job))

    def now_day():
        return datetime.fromtimestamp(deps.now(), tz=timezone.utc).date().isoformat()

    async def idempotent(request, p, fp_body, run):
        """Claim the Idempotency-Key, run once, store the answer. `run` returns (status, body)."""
        key = request.headers.get("idempotency-key")
        if not api_idempotency.valid_key(key):
            raise ApiError("invalid_request")
        try:
            fp = api_idempotency.fingerprint(request.method, request.url.path, fp_body)
        except ValueError:
            raise ApiError("invalid_request")
        now = deps.now()
        rec = await astore.get_request(p["key_id"], key)
        verdict = api_idempotency.decide(key, fp, rec, now)
        if verdict == "conflict":
            raise ApiError("idempotency_conflict")
        if verdict == "replay":
            if rec["state"] == "complete":
                return JSONResponse(rec["response"], status_code=int(rec["status"]),
                                    headers={"X-Request-Id": request.state.api_request_id, "Cache-Control": "no-store", "Idempotency-Replayed": "true"})
            raise ApiError("service_unavailable")         # the same request is still being worked on: retry shortly with the same key
        if rec is not None:
            await astore.replace_expired_request(p["key_id"], key, rec["created_at"])
        if not await astore.claim_request(p["key_id"], key, fp, now):
            raise ApiError("service_unavailable")         # another copy of this request claimed it a moment ago
        try:
            status, body = await run()
        except ApiError:
            await astore.drop_request(p["key_id"], key)           # proven: nothing started, nothing charged
            raise
        except Uncertain:
            raise ApiError("service_unavailable")          # claim kept: the same key can only replay or continue, never start again
        except Exception as ex:
            print(f"[api] unexpected failure in a v1 call: {type(ex).__name__}")
            raise ApiError("internal_error")
        await astore.complete_request(p["key_id"], key, status, body)
        return JSONResponse(body, status_code=status, headers={"X-Request-Id": request.state.api_request_id, "Cache-Control": "no-store"})

    def service_failed(status, data):
        if isinstance(data, dict) and data.get("error") == _UNCERTAIN:
            raise Uncertain()
        raise ApiError(classify(status, data))

    # ---- jobs --------------------------------------------------------------------------------------------
    @api.post("/jobs")
    async def create_job(request: Request, p=Depends(principal("dub"))):
        body = await read_json(request)
        if api_schema.validate_body("CreateJob", body):
            raise ApiError("invalid_request")
        if body["kind"] != "long":
            raise ApiError("invalid_request")             # short dubs arrive in a later phase
        if body["terms_version"] != deps.terms_version:
            raise ApiError("consent_required")
        s = request.state.api_settings

        async def run():
            status, data = await ld.create(request, {"filename": body["filename"], "size": body["size_bytes"],
                                                     "speakers": body.get("stated_speakers", 2), "name": body.get("name", ""),
                                                     "description": body.get("description", "")})
            if status != 200:
                service_failed(status, data)
            ld.tag(data["id"], {"key_id": p["key_id"], "price_percent": s["price_percent"], "revision": 0, "terms_version": body["terms_version"]})
            return 201, view(request, job_of(p, data["id"]))
        return await idempotent(request, p, body, run)

    @api.get("/jobs")
    def list_jobs(request: Request, cursor: str = "", limit: int = 20, p=Depends(principal("read"))):
        if not 1 <= limit <= 100 or len(cursor) > 256:
            raise ApiError("invalid_request")
        jobs = [j for j in ld.jobs_for(p["uid"]) if isinstance(j.get("api"), dict) and not api_jobs.is_gone(j)]
        jobs.sort(key=lambda j: (-float(j.get("created") or 0), j["id"]))
        if cursor:
            try:
                ts, jid = cursor.split("|", 1)
                after = (-float(ts), jid)
            except ValueError:
                raise ApiError("invalid_request")
            jobs = [j for j in jobs if (-float(j.get("created") or 0), j["id"]) > after]
        page, more = jobs[:limit], len(jobs) > limit
        nxt = "%s|%s" % (repr(float(page[-1].get("created") or 0)), page[-1]["id"]) if more and page else None
        return ok(request, {"jobs": [view(request, j) for j in page], "next_cursor": nxt})

    @api.get("/jobs/{job_id}")
    def get_job(job_id: str, request: Request, p=Depends(principal("read"))):
        return ok(request, view(request, job_of(p, job_id)))

    @api.delete("/jobs/{job_id}")
    async def delete_job(job_id: str, request: Request, p=Depends(principal("dub"))):
        job = job_of(p, job_id)
        before = view(request, job)

        async def run():
            status, data = await ld.delete(request, job_id)
            if status != 200:
                service_failed(status, data)
            return 200, dict(before, result_available=False, media_present=False)
        return await idempotent(request, p, {"job_id": job_id}, run)

    # ---- upload ---------------------------------------------------------------------------------------------
    @api.get("/jobs/{job_id}/upload")
    def get_upload(job_id: str, request: Request, p=Depends(principal("read"))):
        return ok(request, api_jobs.upload_view(job_of(p, job_id)))

    @api.put("/jobs/{job_id}/upload/chunks/{index}")
    async def put_chunk(job_id: str, index: int, request: Request, p=Depends(principal("dub"))):
        job = job_of(p, job_id)
        if job.get("status") != "uploading":
            raise ApiError("job_not_ready")
        try:
            clen = int(request.headers.get("content-length") or -1)
        except ValueError:
            clen = -1
        chunk = int(job.get("chunk_bytes") or 0)
        if clen < 0 or clen > chunk:
            raise ApiError("upload_too_large" if clen > chunk else "invalid_request")
        data = await request.body()
        if api_schema.validate_chunk(index, data, int(job["size"]), chunk):
            raise ApiError("invalid_request")
        import hashlib

        async def run():
            status, out = await ld.chunk(request, job_id, index)
            if status != 200:
                service_failed(status, out)
            return 200, api_jobs.upload_view(job_of(p, job_id))
        return await idempotent(request, p, {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}, run)

    # ---- estimate and quotes ------------------------------------------------------------------------------------
    @api.get("/jobs/{job_id}/estimate")
    def get_estimate(job_id: str, request: Request, p=Depends(principal("read"))):
        est = api_jobs.estimate_view(job_of(p, job_id))
        if est is None:
            raise ApiError("job_not_ready")
        return ok(request, est)

    async def numbers_for(request, job, step, keep_music):
        if step == "estimate":
            return api_money.numbers_estimate(int(ld.pricing(job).get("fee", 3)), int(job["paid"].get("fee", 0)))
        if step == "analysis":
            return api_money.numbers_analysis(job["estimate"], job["paid"])
        status, price = await ld.preview(request, job["id"])
        if status != 200:
            service_failed(status, price)
        music = (price.get("music") or {})
        return api_money.numbers_dub(price, int(music.get("max_credits", 0) or 0), keep_music and music.get("kept") is not False)

    def check_state(job, step):
        if job.get("status") != _BEFORE[step]:
            raise ApiError("job_not_ready")
        if step == "estimate" and len(set(job.get("received") or [])) != int(job.get("total_chunks") or 0):
            raise ApiError("upload_incomplete")
        if step == "analysis" and not isinstance(job.get("estimate"), dict):
            raise ApiError("job_not_ready")

    @api.post("/jobs/{job_id}/quotes")
    async def make_quote(job_id: str, request: Request, p=Depends(principal("dub"))):
        body = await read_json(request)
        if api_schema.validate_body("QuoteRequest", body):
            raise ApiError("invalid_request")
        step = body["step"]
        if step not in api_money.LONG_STEPS:
            raise ApiError("invalid_request")             # clone and merge are short-dub steps
        job = job_of(p, job_id)

        async def run():
            check_state(job, step)
            keep = body.get("keep_music", True)
            numbers = await numbers_for(request, job, step, keep)
            now = deps.now()
            q = api_money.build_quote(str(uuid.uuid4()), job["id"], step, numbers, (job.get("api") or {}).get("revision", 0), now,
                                      api_money.price_version(ld.pricing(job), (job.get("api") or {}).get("price_percent", 100)),
                                      deps.terms_version, keep, body.get("tracks", True), [])
            await astore.save_quote(q, p["key_id"], p["uid"], now + api_money.QUOTE_TTL)
            return 200, q
        return await idempotent(request, p, body, run)

    # ---- approvals -------------------------------------------------------------------------------------------------
    async def pay_step(request, p, job_id, step):
        body = await read_json(request)
        if api_schema.validate_body("AcceptQuote", body):
            raise ApiError("invalid_request")
        job = job_of(p, job_id)
        s = request.state.api_settings

        async def run():
            qrow = await astore.get_quote(body["quote_id"], p["key_id"], job_id)
            if not qrow or qrow["step"] != step:
                raise ApiError("not_found")
            quote = qrow["quote"]
            fresh = ld.load(job_id)
            sel = api_money.selection_key(quote["keep_music"], quote["tracks"], quote["speaker_ids"])
            revision = int((fresh.get("api") or {}).get("revision", 0))
            existing = await astore.get_operation(job_id, step, revision, sel)
            if existing is not None:
                # this step was already approved: a retry continues it, anything else is refused
                if existing["quote_id"] != body["quote_id"]:
                    raise ApiError("job_not_ready")
                if fresh.get("status") in _ORDER and _ORDER.index(fresh["status"]) > _ORDER.index(_BEFORE[step]):
                    return 202, view(request, fresh)      # the step already went through: replay the job as it is now
            else:
                check_state(fresh, step)
            live = await numbers_for(request, fresh, step, quote["keep_music"])
            pv = api_money.price_version(ld.pricing(fresh), (fresh.get("api") or {}).get("price_percent", 100))
            err = api_money.check_accept(quote, body, live, pv, revision, float(qrow["expires_ts"]), deps.now())
            if err:
                raise ApiError(err)
            if step in ("analysis", "dub"):
                active = [j for j in ld.jobs_for(p["uid"]) if isinstance(j.get("api"), dict) and j["api"].get("key_id") == p["key_id"]
                          and j.get("status") in _ACTIVE and j["id"] != job_id]
                if len(active) >= s["concurrency_per_key"]:
                    raise ApiError("concurrency_limited")
            reserve = int(live["fixed"]) + int(live["additional_max"])
            op_id = existing["id"] if existing is not None else str(uuid.uuid4())
            def reserve_and_claim():
                with key_lock(p["key_id"]):
                    cap = p.get("daily_credit_cap")
                    if type(cap) is not int or not api_money.cap_check(store.spent_by_others(p["key_id"], now_day(), job_id, step), reserve, cap):
                        raise ApiError("daily_cap_exceeded")
                    if reserve:
                        store.reserve(p["key_id"], now_day(), job_id, step, reserve)
                    if existing is None and not store.claim_operation({"id": op_id, "quote_id": body["quote_id"], "job_id": job_id, "key_id": p["key_id"],
                                                                       "uid": p["uid"], "step": step, "revision": revision, "selection": sel,
                                                                       "maximum_credits": reserve, "state": "accepted"}):
                        raise ApiError("job_not_ready")            # another approval of this step won the claim a moment ago
            await run_in_threadpool(reserve_and_claim)

            async def release():
                if existing is None:
                    await astore.release_operation(op_id)
                if reserve:
                    await astore.release_spend(p["key_id"], job_id, step)
            try:
                if step == "estimate":
                    status, data = await ld.finish(request, job_id)
                elif step == "analysis":
                    status, data = await ld.accept(request, job_id)
                else:
                    status, data = await ld.confirm(request, job_id, int(live["fixed"]), bool(quote["tracks"]), bool(quote["keep_music"]), int(live["additional_max"]))
            except Exception:
                raise Uncertain()
            if status != 200:
                if isinstance(data, dict) and data.get("error") == _UNCERTAIN:
                    raise Uncertain()                           # claim and reservation stay: the website's payment record decides
                await release()
                raise ApiError(classify(status, data))
            ld.tag(job_id, {"op": {"id": op_id, "quote_id": body["quote_id"], "step": step, "maximum_credits": reserve}})
            return 202, view(request, ld.load(job_id))
        return await idempotent(request, p, body, run)

    @api.post("/jobs/{job_id}/upload/finish")
    async def finish_upload(job_id: str, request: Request, p=Depends(principal("dub"))):
        return await pay_step(request, p, job_id, "estimate")

    @api.post("/jobs/{job_id}/accept")
    async def accept(job_id: str, request: Request, p=Depends(principal("dub"))):
        # the step always comes from the stored quote, never from the caller: the body carries the quote id only
        return await pay_step_from_quote(request, p, job_id)

    async def pay_step_from_quote(request, p, job_id):
        body = await read_json(request)
        if api_schema.validate_body("AcceptQuote", body):
            raise ApiError("invalid_request")
        job_of(p, job_id)
        qrow = await astore.get_quote(body["quote_id"], p["key_id"], job_id)
        if not qrow or qrow["step"] not in ("analysis", "dub"):
            raise ApiError("not_found")
        return await pay_step(request, p, job_id, qrow["step"])

    # ---- results ------------------------------------------------------------------------------------------------------
    @api.get("/jobs/{job_id}/results")
    def results(job_id: str, request: Request, p=Depends(principal("read"))):
        job = job_of(p, job_id)
        files = ld.result_files(job) if job.get("status") == "done" else {}
        return ok(request, api_jobs.results_view(files))

    @api.get("/jobs/{job_id}/results/{kind}")
    def download(job_id: str, kind: str, request: Request, p=Depends(principal("read"))):
        if kind not in api_jobs.RESULT_KINDS:
            raise ApiError("not_found")
        job = job_of(p, job_id)
        if job.get("status") != "done":
            raise ApiError("job_not_ready")
        resp = ld.file_response(job, kind)
        if resp is None:
            raise ApiError("result_expired")
        resp.headers["X-Request-Id"] = request.state.api_request_id
        resp.headers["Cache-Control"] = "no-store"
        return resp
