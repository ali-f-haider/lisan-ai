"""Refunds for long videos: one confirmed refund per (payment, reason), recoverable when a reply is lost.

A long video is paid once and pieces of that payment come back at different moments. Each refund gets an ID taken
from the payment it returns plus a fixed name for its reason, so asking again returns the earlier result and grants
nothing twice. Before asking, the request is written to a small file; if the answer is lost the file stays and a
background loop repeats the SAME request later. No provider is ever called from here.
"""
from itertools import islice
import json
import os
from pathlib import Path
import uuid

import credit_billing as cb

MAX_TRIES = 200          # about three hours of once-a-minute tries, then the file waits for a person (admin view)


def refund_id(debit_id, key):
    return str(uuid.uuid5(uuid.UUID(str(debit_id)), "refund:" + str(key)))


def refund(rpc, uid, amount, debit_id, key):
    """The confirmed refund receipt, or None. Safe to repeat with the same arguments."""
    amount = cb.credit_amount(amount)
    debit_id = str(uuid.UUID(str(debit_id)))
    operation_id = refund_id(debit_id, key)
    result = cb.rpc_result(rpc, "lisan_credit_refund_part", {
        "p_operation_id": operation_id, "p_uid": uid, "p_amount": amount, "p_debit_id": debit_id,
        "p_split_rule": "proportional_subscription_up"})
    return cb.receipt(result, uid, amount, "refund", operation_id)


def _folder(store):
    return Path(store) / "credit_refunds"


def _remember(store, record, operation_id):
    folder = _folder(store)
    path = folder / (operation_id + ".json")
    temporary = folder / (operation_id + "." + uuid.uuid4().hex + ".tmp")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        pass            # the ledger is still safe to ask again; only the automatic retry is lost
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return path


def settle(rpc, store, uid, amount, debit_id, key):
    """Write the request down, ask the ledger, forget the request once it is confirmed."""
    amount = cb.credit_amount(amount)
    debit_id = str(uuid.UUID(str(debit_id)))
    operation_id = refund_id(debit_id, key)
    path = _remember(store, {"uid": uid, "amount": amount, "debit_id": debit_id, "key": str(key), "tries": 0}, operation_id)
    confirmed = refund(rpc, uid, amount, debit_id, key)
    if confirmed is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return confirmed


def reconcile(rpc, store):
    """Repeat recorded refunds that were never confirmed. Free: no provider call, no new charge."""
    folder = _folder(store)
    if not folder.exists():
        return 0
    done = 0
    for path in islice(folder.glob("*.json"), 100):
        try:
            if path.stat().st_size > 65536:
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            if path.stem != refund_id(record["debit_id"], record["key"]):
                continue
            tries = int(record.get("tries", 0))
            if tries >= MAX_TRIES:
                continue
            if refund(rpc, record["uid"], record["amount"], record["debit_id"], record["key"]) is not None:
                path.unlink(missing_ok=True)
                done += 1
            else:
                record["tries"] = tries + 1
                _remember(store, record, path.stem)
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return done


def legacy(rpc, record_spend, uid, amount, job_id):
    """Refund of a payment made before payments carried an ID: the old single-shot grant. The history row is
    written only after the grant is confirmed. Repeating this is NOT safe, which is why newer payments never use it."""
    amount = int(amount)
    try:
        balance = rpc("add_credits", {"uid": uid, "amount": amount}, strict=True)
    except Exception:
        return None
    if type(balance) is not int or balance < amount:
        return None             # a missing profile answers 0; anything below the refund is not a confirmed grant
    record_spend(uid, "long_dub_refund", -amount, job_id)
    return {"legacy": True, "balance": balance}
