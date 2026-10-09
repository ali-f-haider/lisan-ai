"""Pure per-key guards. Caller MUST lock, read and persist atomically.

No lease expires automatically: lost workers require receipt reconciliation.
Daily caps count gross charges, not charges minus refunds. Pending reservations
survive midnight and restarts. Do not discard this state on a timeout.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math


def _number(value, positive=False, integer=False):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or value < (1 if positive else 0) or value > 253402300799
            or (integer and type(value) is not int)):
        raise ValueError("Invalid limit or state.")
    return value


def _key(key_id):
    if not isinstance(key_id, str) or not 1 <= len(key_id) <= 128:
        raise ValueError("Invalid key identifier.")


def rate_limit(key_id, now, config, state):
    """(allowed, retry_after_seconds, new_state); config capacity/refill_per_second.

    Denied requests do not consume tokens. Time regression cannot mint tokens.
    Missing or malformed persisted state is an error, not a fresh allowance.
    """
    _key(key_id)
    _number(now)
    capacity = _number(config["capacity"], positive=True, integer=True)
    refill = _number(config["refill_per_second"], positive=False)
    if not refill:
        raise ValueError("Refill must be positive.")
    result = deepcopy(state)
    entry = result.get(key_id)
    if entry is None:
        entry = {"tokens": float(capacity), "at": now}
    if not isinstance(entry, dict):
        raise ValueError("Invalid limiter state.")
    tokens = _number(entry["tokens"])
    last = _number(entry["at"])
    tokens = min(capacity, tokens + max(0, now - last) * refill)
    allowed = tokens >= 1
    result[key_id] = {"tokens": tokens - 1 if allowed else tokens, "at": max(now, last)}
    retry = 0 if allowed else max(1, math.ceil((1 - tokens) / refill + max(0, last - now)))
    return allowed, retry, result


def acquire(key_id, operation_id, cap, state):
    """Concurrency is held until release, including queued and recovering jobs."""
    _key(key_id)
    _key(operation_id)
    _number(cap, positive=True, integer=True)
    result = deepcopy(state)
    active = result.get(key_id, [])
    if not isinstance(active, list) or any(not isinstance(x, str) for x in active):
        raise ValueError("Invalid concurrency state.")
    if operation_id in active:
        return True, 0, result
    if len(active) >= cap:
        return False, 1, result
    result[key_id] = active + [operation_id]
    return True, 0, result


def release(key_id, operation_id, state):
    _key(key_id)
    _key(operation_id)
    result = deepcopy(state)
    active = result.get(key_id, [])
    if not isinstance(active, list):
        raise ValueError("Invalid concurrency state.")
    result[key_id] = [x for x in active if x != operation_id]
    return result


def reserve_credits(key_id, operation_id, amount, now, cap, state):
    """Reserve the whole quote maximum before any debit; zero costs remain free.

    Each pending reservation counts against EVERY new day until reconciled;
    this prevents crossing midnight with unfinished spending to evade the cap.
    A repeated operation can never enlarge or replace its reservation.
    """
    _key(key_id)
    _key(operation_id)
    _number(amount, integer=True)
    _number(cap, integer=True)
    _number(now)
    day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    result = deepcopy(state)
    entries = result.setdefault(key_id, {})
    if not isinstance(entries, dict):
        raise ValueError("Invalid budget state.")
    existing = entries.get(operation_id)
    if existing is not None:
        return existing.get("maximum") == amount and existing.get("state") != "released", 0, result
    spent = 0
    for item in entries.values():
        if not isinstance(item, dict) or item.get("state") not in ("pending", "settled", "released"):
            raise ValueError("Invalid budget state.")
        _number(item["maximum"], integer=True)
        _number(item["charged"], integer=True)
        if item["state"] == "pending":
            spent += item["maximum"]
        elif item["day"] == day and item["state"] == "settled":
            spent += item["charged"]
    if amount and spent + amount > cap:
        return False, max(1, math.ceil((int(now) // 86400 + 1) * 86400 - now)), result
    entries[operation_id] = {"maximum": amount, "charged": 0, "day": day, "state": "pending"}
    return True, 0, result


def settle_credits(key_id, operation_id, charged, now, state):
    """Only after durable receipts prove the gross debit (including later refunds).

    Zero means a proved no-debit, released reservation. A settled operation is
    immutable; duplicate settlement is safe. Use actual debit date in now.
    """
    _key(key_id)
    _key(operation_id)
    _number(charged, integer=True)
    _number(now)
    result = deepcopy(state)
    entry = result.get(key_id, {}).get(operation_id)
    if not isinstance(entry, dict):
        raise ValueError("Missing credit reservation.")
    if charged > entry["maximum"]:
        raise ValueError("The charge exceeds its reserved maximum.")
    if entry["state"] != "pending":
        if entry["charged"] != charged:
            raise ValueError("Settlement conflicts with its receipt.")
        return result
    entry.update(charged=charged, state="settled" if charged else "released",
                 day=datetime.fromtimestamp(now, timezone.utc).date().isoformat())
    return result
