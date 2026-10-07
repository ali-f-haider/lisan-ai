"""Read-only, field-whitelisted admin billing diagnostics."""
import json
import uuid
from urllib.error import HTTPError

CATEGORIES = ("pending", "refunds_owed", "legacy_packs", "clone_refunds")


def load_health(rpc):
    try:
        result = rpc("lisan_billing_health", {}, strict=True)
    except HTTPError as ex:
        try:
            detail = json.loads(ex.read().decode("utf-8"))
        except (ValueError, OSError, AttributeError):
            detail = {}
        if ex.code == 404 or detail.get("code") == "PGRST202":
            return {"installed": False, "message": "Not installed yet"}
        return {"installed": None, "message": "Billing health could not be read. Please try again."}
    except Exception:
        return {"installed": None, "message": "Billing health could not be read. Please try again."}
    if isinstance(result, list) and len(result) == 1:
        result = result[0]
    if not isinstance(result, dict) or not isinstance(result.get("lists"), dict):
        return {"installed": None, "message": "Billing health could not be read. Please try again."}
    flags = result.get("ready") or {}
    ready = isinstance(flags, dict) and flags.get("version") == 1 and all(
        flags.get(key) is True for key in ("debit", "pack", "refund", "cancel"))
    output = {"installed": True, "ready": ready, "lists": {}}
    for key in CATEGORIES:
        group = result["lists"].get(key)
        if (not isinstance(group, dict) or type(group.get("count")) is not int or
                group["count"] < 0 or not isinstance(group.get("rows"), list)):
            return {"installed": None, "message": "Billing health could not be read. Please try again."}
        rows = []
        for row in group["rows"][:20]:
            try:
                ids = {field: str(uuid.UUID(str(row[field]))) for field in ("operation_id", "account_id")}
                if (row["kind"] not in ("debit", "refund", "pack") or
                        any(type(row[field]) is not int or row[field] < 0 for field in ("amount", "age_seconds"))):
                    continue
                rows.append(dict(ids, amount=row["amount"], age_seconds=row["age_seconds"], kind=row["kind"]))
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        output["lists"][key] = {"count": group["count"], "rows": rows}
    return output
