"""Durable short-line request receipts; import has no network or workers.

Only hashes and outcomes are kept, never additional audio takes or API keys.
The caller holds the existing project audio lock for the entire operation.
"""
import hashlib
import json
import os
from itertools import islice
from itertools import chain
from pathlib import Path
import re
import uuid

from credit_billing import credit_amount, rpc_result
from shortdub_billing import debit_confirmed
from shortdub_paths import operation_active

MAX_RECORDS = 1000
MAX_RECORD_BYTES = 65536
_JOB = re.compile(r"[A-Za-z0-9_-]{20,100}")


class RequestProblem(Exception):
    def __init__(self, message, status=409, **details):
        self.status = status
        self.payload = dict(error=message, **details)
        super().__init__(message)


def work_details(req):
    def value(row):
        if hasattr(row, "model_dump"):
            return row.model_dump()
        if hasattr(row, "dict"):
            return row.dict()
        return dict(vars(row))
    data = {"job_id": req.job_id, "segment": value(req.segment),
            "segments": [value(row) for row in req.segments],
            "voice_id": req.voice_id, "tempo_mode": req.tempo_mode,
            "duration_mode": req.duration_mode, "total_duration": req.total_duration,
            "overlap_allowed": getattr(req, "overlap_allowed", {}),
            "dead_space_allowed": getattr(req, "dead_space_allowed", {})}
    try:
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError):
        raise RequestProblem("Please check this line's text and timings, then try again.", 400) from None
    return hashlib.sha256(encoded).hexdigest()


def operation_path(output, job_id, operation_id):
    if not _JOB.fullmatch(str(job_id or "")):
        raise RequestProblem("We could not find this project.", 404)
    try:
        operation_id = str(uuid.UUID(str(operation_id)))
    except (ValueError, TypeError, AttributeError):
        raise RequestProblem("Please reload this page before re-speaking the line.", 400) from None
    return Path(output) / (job_id + "_regenerations") / (operation_id + ".json")


def clear_receipts(output, job_id):
    folder = operation_path(output, job_id, uuid.UUID(int=0)).parent
    # Resolve before deleting; never follow a redirected project folder.
    if not folder.exists() or folder.resolve().parent != Path(output).resolve() or operation_active(job_id):
        return
    for path in folder.iterdir():
        if path.is_file() and re.fullmatch(r"[0-9a-f-]{36}(?:\.[0-9a-f]{32}\.tmp|\.json)", path.name):
            path.unlink(missing_ok=True)
    try:
        folder.rmdir()
    except OSError:
        pass


def maintain_receipts(output, upload):
    """Keep receipts while job assets exist; clean them with the project.

    The existing generic folder sweep may otherwise erase them after its
    working-file cutoff while a longer-lived final export still exists.
    """
    output, upload = Path(output), Path(upload)
    for folder in output.glob("*_regenerations"):
        job_id = folder.name.removesuffix("_regenerations")
        if not _JOB.fullmatch(job_id) or not folder.is_dir() or folder.resolve().parent != output.resolve():
            continue
        assets = chain(output.glob(job_id + ".*"), output.glob(job_id + "_*"),
                       upload.glob(job_id + ".*"), upload.glob(job_id + "_*"))
        if operation_active(job_id) or any(path != folder for path in assets):
            os.utime(folder, None)
        else:
            clear_receipts(output, job_id)


def read(path):
    try:
        if path.stat().st_size > MAX_RECORD_BYTES:
            raise ValueError
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise ValueError
        return record
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError):
        raise RequestProblem("This request's saved result could not be read. Please contact support before retrying.", 503) from None


def write(path, record):
    temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) > MAX_RECORD_BYTES:
            raise ValueError
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError, TypeError):
        raise RequestProblem("We could not save this request safely. Please contact support before retrying.", 503) from None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def audio_receipt(paths):
    output = {}
    for path in paths:
        path = Path(path)
        if not path.is_file():
            raise RequestProblem("The re-spoken audio could not be saved. Please contact support before retrying.", 503)
        # Stream instead of loading an entire generated mix into memory.
        with path.open("rb") as stream:
            output[path.name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return output


def check_record(record, uid, fingerprint, accepted):
    if (record.get("uid") != uid or record.get("work") != fingerprint or
            type(accepted) is not int or record.get("amount") != accepted):
        raise RequestProblem("This request ID belongs to different work. Reload the project before starting a new take.")


def replay(output, record, settle):
    status = record.get("status")
    if status == "done":
        files = record.get("audio") or {}
        if not files:
            raise RequestProblem("This take's audio is no longer available. Nothing was charged again.", 410, operation_complete=True)
        try:
            current = audio_receipt([Path(output) / name for name in files])
        except (OSError, RequestProblem):
            current = {}
        if current != files:
            raise RequestProblem("This take's audio is no longer available. Nothing was charged again.", 410, operation_complete=True)
        return dict(record["result"], replayed=True, operation_complete=True)
    if status == "failed":
        confirmed = settle(record["operation_id"], "failed", record["amount"])
        refunded = record["amount"] if confirmed is not None else 0
        return {"status": "error", "error": ("The line could not be re-spoken. Your credits were refunded." if refunded else
                "The line could not be re-spoken. Your refund is pending and will be retried automatically."),
                "credits_charged": record["amount"] - refunded, "credits_refunded": refunded,
                "refund_pending": record["amount"] - refunded, "operation_id": record["operation_id"],
                "operation_complete": True}
    if status == "refused":
        raise RequestProblem(record["error"], record.get("http_status", 409), operation_complete=True)
    if status in ("generating", "unknown"):
        raise RequestProblem("This request's result could not be confirmed. Please contact support before starting another take.", 503,
                             operation_id=record["operation_id"])
    return None


def run(output, req, uid, prepare, charge, generate, settle, rpc, audio_paths):
    path = operation_path(output, req.job_id, getattr(req, "operation_id", None))
    operation_id = path.stem
    fingerprint = work_details(req)
    record = read(path)
    if record is not None:
        check_record(record, uid, fingerprint, req.accepted_credits)
        result = replay(output, record, settle)
        if result is not None:
            return result
    else:
        # Keep every receipt until project cleanup. Never evict a paid ID to
        # make space: an evicted ID might otherwise be mistaken for new work.
        if path.parent.exists() and sum(1 for _ in islice(path.parent.glob("*.json"), MAX_RECORDS)) >= MAX_RECORDS:
            raise RequestProblem("This project has reached its re-speaking limit. Please contact support.", 409)
        amount = credit_amount(prepare())
        record = {"operation_id": operation_id, "uid": uid, "work": fingerprint,
                  "amount": amount, "status": "created", "voice_engine": req.voice_engine}
        write(path, record)
    if record["status"] == "created":
        existing = rpc_result(rpc, "lisan_credit_begin", {"p_operation_id": operation_id,
                              "p_uid": uid, "p_kind": "debit", "p_amount": record["amount"], "p_debit_id": None})
        if not existing:
            raise RequestProblem("Credit payments are temporarily paused. Please retry this same request later.", 503)
        if (existing.get("uid") != uid or existing.get("amount") != record["amount"] or
                existing.get("operation_id") != operation_id or existing.get("kind") != "debit"):
            raise RequestProblem("This payment request could not be verified. Please contact support.", 503)
        if existing.get("status") != "pending":
            # SQL is authoritative even if project cleanup removed an old
            # local outcome. Never rerun a provider against a completed ID.
            record.update(status="refused", error="This earlier request has no available saved result. Nothing was charged again.", http_status=410)
            write(path, record)
            raise RequestProblem(record["error"], 410, operation_complete=True)
        record["status"] = "charging"
        write(path, record)
    if record["status"] == "charging":
        balance = charge(record["amount"], operation_id)
        if balance is False:
            record.update(status="refused", error="You do not have enough credits for this line. Add credits, then start a new take.", http_status=402)
            write(path, record)
            raise RequestProblem(record["error"], 402, operation_complete=True)
        if not debit_confirmed(balance):
            raise RequestProblem("Payment could not be confirmed. Retry this same request; no audio generation has started.", 503)
        record.update(status="paid", balance_after=balance)
        write(path, record)
    if record["status"] != "paid":
        raise RequestProblem("This request needs review. Please contact support before retrying.", 503)
    req.voice_engine = record["voice_engine"]
    # Write BEFORE starting the paid provider. A restart here must never
    # blindly generate again: a provider may have accepted the first call.
    record["status"] = "generating"
    write(path, record)
    try:
        result = generate()
    except Exception:
        record["status"] = "unknown"
        write(path, record)
        raise RequestProblem("The request was interrupted. Please contact support before retrying.", 503) from None
    if not isinstance(result, dict) or result.get("status") != "success" or result.get("error"):
        record["status"] = "failed"
        try:
            write(path, record)
        except RequestProblem:
            # A full-failure refund still gets the independent Job 5 journal
            # even if this project's checkpoint cannot be saved.
            settle(operation_id, "failed", record["amount"])
            raise
        return replay(output, record, settle)
    try:
        record["audio"] = audio_receipt(audio_paths())
    except (OSError, RequestProblem):
        record["status"] = "unknown"
        write(path, record)
        raise RequestProblem("The generated audio could not be confirmed. Please contact support before retrying.", 503) from None
    record.update(status="done", result=dict(result, credits_charged=record["amount"],
                  balance_after=record["balance_after"], operation_id=operation_id, operation_complete=True))
    write(path, record)
    settle(operation_id, "delivered", 0)
    return record["result"]
