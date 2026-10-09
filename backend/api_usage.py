"""Aggregate a pre-authorized, per-key projection of durable credit events.

Legacy credit_spends lacks key ids and operation ids: join to the API operation
records first. Never guess attribution from a job id or a filename. `calls`
means paid operations, NOT all HTTP requests. Free request counts are separate.
"""
from datetime import datetime, timezone


def usage_report(events, key_id, start_day, end_day):
    """Inclusive UTC dates. Deduplicate receipts, retain separate refund days.

    Input projection: api_key_id, operation_id, kind=debit/refund, amount,
    created_at (ISO 8601 with timezone), status=done. Refund amount is positive.
    Unknown/unattributed events are omitted; malformed matching events raise
    ValueError rather than silently under-report money. Conflicting duplicates
    also raise. No caller-controlled content is returned.
    """
    if not isinstance(key_id, str) or not key_id:
        raise ValueError("A key identifier is required.")
    try:
        first = datetime.strptime(start_day, "%Y-%m-%d").date()
        last = datetime.strptime(end_day, "%Y-%m-%d").date()
        if first.isoformat() != start_day or last.isoformat() != end_day or last < first or (last - first).days > 365:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Use an inclusive UTC date range of at most 366 days.") from None
    totals = {"calls": 0, "credits_spent": 0, "credits_refunded": 0, "net_credits": 0}
    days, seen = {}, {}
    for event in events:
        if not isinstance(event, dict) or event.get("api_key_id") != key_id or event.get("status") != "done":
            continue
        try:
            op, kind, amount = event["operation_id"], event["kind"], event["amount"]
            at = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
            if (not isinstance(op, str) or not op or kind not in ("debit", "refund")
                    or type(amount) is not int or amount < 0 or at.tzinfo is None):
                raise ValueError
            day = at.astimezone(timezone.utc).date().isoformat()
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            raise ValueError("A matching credit event is invalid.") from None
        signature = (kind, amount, day)
        if op in seen:
            if seen[op] != signature:
                raise ValueError("Conflicting credit receipts.")
            continue
        seen[op] = signature
        if not start_day <= day <= end_day:
            continue
        row = days.setdefault(day, dict(calls=0, credits_spent=0, credits_refunded=0, net_credits=0))
        column = "credits_spent" if kind == "debit" else "credits_refunded"
        row[column] += amount
        row["calls"] += int(kind == "debit")
        row["net_credits"] += amount if kind == "debit" else -amount
    for row in days.values():
        for column in totals:
            totals[column] += row[column]
    return dict(totals, by_day=[dict(day=day, **days[day]) for day in sorted(days)],
                calls_meaning="paid_operations", start_day=start_day, end_day=end_day)
