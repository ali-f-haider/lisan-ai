"""Read-only, field-whitelisted admin billing diagnostics."""
import json
import uuid
from urllib.error import HTTPError

CATEGORIES = ("pending", "refunds_owed", "legacy_packs", "clone_refunds")
EXTRA_CATEGORIES = ("admin_pending", "subscription_pending", "subscription_stale", "receipt_mismatches", "subscription_legacy")


def missing(ex):
    try:
        detail = json.loads(ex.read().decode("utf-8"))
    except (ValueError, OSError, AttributeError):
        detail = {}
    return ex.code == 404 or (isinstance(detail, dict) and detail.get("code") == "PGRST202")


def load_health(rpc):
    unavailable = {"installed": None, "message": "Billing health could not be read. Please try again."}
    extended = True
    try:
        try:
            result = rpc("lisan_billing_health_v2", {}, strict=True)
        except HTTPError as ex:
            if not missing(ex):
                return unavailable
            extended = False
            result = rpc("lisan_billing_health", {}, strict=True)
    except HTTPError as ex:
        return {"installed": False, "message": "Not installed yet"} if missing(ex) else unavailable
    except Exception:
        return unavailable
    if isinstance(result, list) and len(result) == 1:
        result = result[0]
    if not isinstance(result, dict) or not isinstance(result.get("lists"), dict):
        return unavailable
    flags = result.get("ready") or {}
    names = ("debit", "pack", "refund", "cancel") + (("admin_adjust", "subscription_grant") if extended else ())
    ready = extended and isinstance(flags, dict) and flags.get("version") == (2 if extended else 1) and all(flags.get(key) is True for key in names)
    output = {"installed": True, "ready": ready, "extended": extended,
              "signup_configured": flags.get("signup_credits") is True if extended and isinstance(flags, dict) else None, "lists": {}}
    for key in CATEGORIES + (EXTRA_CATEGORIES if extended else ()):
        group = result["lists"].get(key)
        if (not isinstance(group, dict) or type(group.get("count")) is not int or
                group["count"] < 0 or not isinstance(group.get("rows"), list)):
            return unavailable
        rows = []
        for row in group["rows"][:20]:
            try:
                ids = {field: str(uuid.UUID(str(row[field]))) for field in ("operation_id", "account_id")}
                unknown_age = key == "subscription_legacy" and row.get("age_seconds") is None
                if (row["kind"] not in ("debit", "refund", "pack", "admin_adjust", "subscription_grant", "subscription_stale", "subscription_legacy", "admin_mismatch", "invoice_mismatch") or
                        type(row["amount"]) is not int or row["amount"] < 0 or
                        (not unknown_age and (type(row["age_seconds"]) is not int or row["age_seconds"] < 0))):
                    continue
                rows.append(dict(ids, amount=row["amount"], age_seconds=row["age_seconds"], kind=row["kind"]))
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        output["lists"][key] = {"count": group["count"], "rows": rows}
    return output
