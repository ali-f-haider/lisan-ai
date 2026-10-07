"""Server-side billing helpers; no network or configuration on import.

SQL owns balances and receipts. Transport retries always keep the same ID.
An unavailable RPC must never fall back to the old two-transaction debit.
"""
from decimal import Decimal, InvalidOperation
import json
from itertools import islice
import math
import os
from pathlib import Path
import uuid
from urllib.error import HTTPError

UNAVAILABLE = "Credit payments are temporarily paused. Please try again later."
MAX_CREDITS = 2147483647


def number(value, label, *, zero=False, integer=False, maximum=MAX_CREDITS):
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or (not zero and n == 0) or n > maximum:
            raise ValueError
        if integer and n != n.to_integral_value():
            raise ValueError
    except (ValueError, InvalidOperation, TypeError):
        rule = "a non-negative" if zero else "a positive"
        unit = " whole number" if integer else " number"
        raise ValueError(f"{label} must be {rule}{unit}.") from None
    result = int(n) if integer else float(n)
    if not zero and result == 0:
        raise ValueError(f"{label} is too small; enter a larger number.")
    return result


def credit_amount(value):
    return number(value, "Credit amount", integer=True)


def validate_pricing(config):
    """Reject an invalid save before ANY database write; preserve other fields."""
    if not isinstance(config, dict):
        raise ValueError("Enter valid pricing values and save again.")
    out = dict(config)
    # Zero means no bonus/reserve, no local assembly fee, or an explicitly
    # waived long-analysis/processing fee. Voice/transcription rates stay paid.
    free = {"freeCredits", "minReserve", "mergeCredits", "longDubFlatCredits"}
    whole = {"freeCredits", "minReserve", "transcribeCredits", "mergeCredits",
             "cloneCredits", "inworldCloneCredits", "subscriptionCredits",
             "charsPerCredit", "inworldCharsPerCredit", "longDubFlatCredits"}
    labels = {"freeCredits": "Signup credits", "minReserve": "Credit reserve",
              "transcribeCredits": "Transcription price", "mergeCredits": "Assembly price",
              "cloneCredits": "Cloning price", "inworldCloneCredits": "Alternative cloning price",
              "subscriptionCredits": "Monthly credits", "charsPerCredit": "Characters per credit",
              "inworldCharsPerCredit": "Alternative characters per credit",
              "longDubFlatCredits": "Processing price", "lipsyncCreditsPerSec": "Lip-sync price",
              "subscriptionPriceUsd": "Monthly price", "longDubAnalysisPerMin": "Analysis price",
              "geminiCreditsPerCent": "Text-service price factor", "musicFillCredits": "Music repair price"}
    for key, label in labels.items():
        if key in out:
            out[key] = number(out[key], label, zero=key in free or key == "longDubAnalysisPerMin",
                              integer=key in whole or key == "musicFillCredits")
    if "geminiCreditsPerCent" in out and not 0.1 <= out["geminiCreditsPerCent"] <= 100:
        raise ValueError("Text-service price factor must be between 0.1 and 100.")
    for key in ("packs", "subscriptionPlans"):
        if key not in out:
            continue
        if not isinstance(out[key], list):
            raise ValueError("Enter a valid list of packs and monthly plans.")
        rows = []
        for item in out[key]:
            if not isinstance(item, dict):
                raise ValueError("Enter valid pack and monthly plan prices.")
            row = dict(item)
            row["price_usd"] = number(row.get("price_usd"), "Pack or monthly price")
            if row["price_usd"] < 0.01 or round(row["price_usd"], 2) != row["price_usd"]:
                raise ValueError("Pack and monthly prices must use whole cents, at least 0.01.")
            credits = "credits" if key == "packs" else "credits_per_month"
            row[credits] = number(row.get(credits), "Pack or monthly credits", integer=True)
            for field in ("bonus_pct", "voice_slots", "clones_per_month", "storage_gb"):
                if field in row:
                    row[field] = number(row[field], "Bonus or plan allowance", zero=True,
                                        integer=field != "storage_gb")
            rows.append(row)
        out[key] = rows
    if "assistant" in out:
        value = out["assistant"]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                raise ValueError("Enter valid helper prices and limits.") from None
        if not isinstance(value, dict):
            raise ValueError("Enter valid helper prices and limits.")
        value = dict(value)
        for key, limit in (("creditsPerCent", 100), ("dailyBudgetUsd", 1000)):
            if key in value:
                value[key] = number(value[key], "Helper price or budget", zero=True, maximum=limit)
        out["assistant"] = value
    try:
        json.dumps(out, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("Enter valid pricing values and save again.") from None
    return out


def rpc_result(rpc, name, args):
    """Two bounded attempts; retries share args, including their durable ID."""
    for attempt in range(2):
        try:
            result = rpc(name, args, strict=True)
            if isinstance(result, list) and len(result) == 1:
                result = result[0]
            if isinstance(result, dict):
                return result
        except HTTPError as ex:
            # Missing SQL, denied access and invalid arguments never trigger
            # any legacy fallback. The server remains safe before migration.
            if ex.code < 500:
                return None
        except Exception:
            pass
    return None


def ready(rpc):
    result = rpc_result(rpc, "lisan_billing_ready", {})
    return bool(result and result.get("version") == 1 and
                all(result.get(k) is True for k in ("debit", "pack", "refund", "cancel")))


def receipt(result, uid, amount, kind, operation_id):
    if not isinstance(result, dict) or type(result.get("amount")) is not int:
        return None
    if (result.get("uid") != uid or result.get("amount") != amount or
            result.get("kind") != kind or result.get("operation_id") != operation_id):
        return None
    if result.get("status") != "done":
        return None
    keys = ("taken_subscription", "taken_permanent", "subscription_balance", "permanent_balance")
    if any(type(result.get(k)) is not int or result[k] < 0 for k in keys):
        return None
    if result["taken_subscription"] + result["taken_permanent"] != amount:
        return None
    return result


def debit(rpc, uid, amount, action="deduction", job_id=None, generated_seconds=None, operation_id=None):
    amount = credit_amount(amount)
    if not uid:
        return None
    operation_id = str(uuid.UUID(str(operation_id))) if operation_id else str(uuid.uuid4())
    if generated_seconds is not None:
        generated_seconds = float(generated_seconds)
        if not math.isfinite(generated_seconds) or generated_seconds < 0:
            raise ValueError("Audio duration must be a non-negative number.")
    args = {"p_operation_id": operation_id, "p_uid": uid, "p_amount": amount,
            "p_action": action or "deduction", "p_job_id": job_id,
            "p_generated_seconds": generated_seconds}
    result = rpc_result(rpc, "lisan_atomic_debit", args)
    if (result and result.get("status") == "failed" and result.get("reason") == "insufficient" and
            result.get("uid") == uid and result.get("operation_id") == operation_id and
            result.get("amount") == amount and result.get("kind") == "debit"):
        return False
    confirmed = receipt(result, uid, amount, "debit", operation_id)
    return confirmed["subscription_balance"] + confirmed["permanent_balance"] if confirmed else None


def fulfill(rpc, uid, session_id, amount):
    amount = credit_amount(amount)
    if not uid or not session_id:
        return None
    result = rpc_result(rpc, "lisan_fulfill_pack", {"p_uid": uid, "p_receipt": session_id, "p_amount": amount})
    if not result or result.get("uid") != uid or result.get("receipt") != session_id or result.get("amount") != amount:
        return None
    if result.get("status") == "legacy_review":
        return "needs-review"
    if result.get("status") == "already_fulfilled":
        return "already-fulfilled"
    if result.get("status") == "done" and type(result.get("permanent_balance")) is int and result["permanent_balance"] >= 0:
        return result["permanent_balance"]
    return None


def refund(rpc, uid, amount, debit_id):
    amount = credit_amount(amount)
    debit_id = str(uuid.UUID(str(debit_id)))
    operation_id = str(uuid.uuid5(uuid.UUID(debit_id), "clone-refund"))
    result = rpc_result(rpc, "lisan_credit_refund", {"p_operation_id": operation_id,
                        "p_uid": uid, "p_amount": amount, "p_debit_id": debit_id,
                        "p_split_rule": "proportional_subscription_up"})
    return receipt(result, uid, amount, "refund", operation_id)


def clone_refund_amount(fee, failed, total):
    fee = credit_amount(fee)
    if type(total) is not int or total < 1 or type(failed) is not int or not 0 <= failed <= total:
        raise ValueError("Invalid speaker count.")
    return (fee * failed + total - 1) // total


def settle_clone(rpc, store, uid, debit_id, outcome, refund_due):
    """Journal the known provider outcome before trying to settle/refund it.

    A failed/lost database reply leaves a small recoverable local record.
    No provider call is repeated by reconciliation.
    """
    debit_id = str(uuid.UUID(str(debit_id)))
    refund_due = number(refund_due, "Refund amount", zero=True, integer=True)
    if outcome not in ("delivered", "partial", "failed", "cancelled"):
        raise ValueError("Invalid cloning outcome.")
    record = {"uid": uid, "debit_id": debit_id, "outcome": outcome, "refund_due": refund_due}
    folder = Path(store) / "credit_settlements"
    path = folder / (debit_id + ".json")
    temporary = folder / (debit_id + "." + uuid.uuid4().hex + ".tmp")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        # SQL still provides durable recovery if its settlement commits.
        # Both unavailable means the response carries the debit ID for review.
        pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    if outcome == "cancelled":
        result = rpc_result(rpc, "lisan_cancel_clone_debit", {"p_operation_id": debit_id,
                            "p_uid": uid, "p_amount": refund_due})
        if (result and result.get("uid") == uid and result.get("operation_id") == debit_id and
                result.get("kind") == "debit" and result.get("amount") == refund_due and result.get("status") == "failed"):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return {"not_charged": True}
        outcome = "failed"
    else:
        result = rpc_result(rpc, "lisan_credit_finish", {"p_operation_id": debit_id,
                            "p_work_status": outcome, "p_refund_due": refund_due})
    if not (result and result.get("uid") == uid and result.get("operation_id") == debit_id and
            result.get("kind") == "debit" and result.get("status") == "done" and
            result.get("work_status") == outcome and result.get("refund_due") == refund_due):
        return None
    confirmed = refund(rpc, uid, refund_due, debit_id) if refund_due else result
    if confirmed is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return confirmed


def reconcile_clones(rpc, store):
    """Free recovery of recorded settlements; never creates/clones a voice."""
    folder = Path(store) / "credit_settlements"
    if folder.exists():
        for path in islice(folder.glob("*.json"), 100):
            try:
                if path.stat().st_size > 65536:
                    continue
                record = json.loads(path.read_text(encoding="utf-8"))
                if path.stem != str(uuid.UUID(record["debit_id"])):
                    continue
                settle_clone(rpc, store, **record)
            except (OSError, ValueError, TypeError, KeyError):
                continue
    try:
        pending = rpc("lisan_pending_clone_refunds", {}, strict=True)
    except Exception:
        return
    if not isinstance(pending, list):
        return
    for record in pending[:100]:
        try:
            refund(rpc, record["uid"], record["refund_due"], record["operation_id"])
        except (ValueError, TypeError, KeyError):
            continue
