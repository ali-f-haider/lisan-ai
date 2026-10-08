"""Offline transactional mock of the Job 7 RPC contract; SQL is tested separately."""
import copy
import uuid
from datetime import datetime, timezone

UID = "00000000-0000-4000-8000-000000000001"
OP = "00000000-0000-4000-8000-000000000002"
START = "2026-10-01T00:00:00+00:00"
END = "2026-11-01T00:00:00+00:00"


class Actions:
    def __init__(self, state=None):
        self.state = state if state is not None else {"credits": 100, "subscription": 37, "permanent": 100}
        self.admin, self.invoices, self.history = {}, {}, []
        self.fail = False
        self.before_admin = None
        self.history_write = lambda *args, **kwargs: True
        self.audit_write = lambda *args, **kwargs: True
        self.calls = []

    def rpc(self, name, a, strict=False):
        self.calls.append((name, copy.deepcopy(a)))
        if name == "lisan_subscription_ready":
            return {"ready": not self.fail}
        if name.startswith("lisan_admin_"):
            op = self.admin.setdefault(a["p_operation_id"], {"operation_id": a["p_operation_id"],
                "uid": a["p_uid"], "requested_delta": a["p_delta"], "reason": a["p_reason"], "status": "pending"})
            if (op["uid"], op["requested_delta"], op["reason"]) != (a["p_uid"], a["p_delta"], a["p_reason"]):
                return {"status": "mismatch"}
            if name == "lisan_admin_begin" or op["status"] == "done":
                return copy.deepcopy(op)
            if self.fail:
                raise OSError("Synthetic transaction outage")
            if self.before_admin:
                hook, self.before_admin = self.before_admin, None
                hook()
            before = self.state["credits"]
            if before is None:
                raise OSError("Synthetic missing balance")
            after = max(0, before + a["p_delta"])
            if not self.history_write(a["p_uid"], "admin_adjustment", before-after, job_id=None, reason=a["p_reason"]):
                raise OSError("Synthetic history outage")
            if not self.audit_write(a["p_uid"], after-before, a["p_reason"]):
                raise OSError("Synthetic audit outage")
            self.state["credits"] = after
            self.history.append({"action": "admin_adjustment", "credits": before-after})
            op.update(status="done", balance_before=before, balance_after=after, actual_delta=after-before)
            return copy.deepcopy(op)
        if name.startswith("lisan_subscription_"):
            op = self.invoices.setdefault(a["p_invoice_id"], {"invoice_id": a["p_invoice_id"], "uid": a["p_uid"],
                "amount": a["p_amount"], "period_start": a["p_period_start"], "period_end": a["p_period_end"], "status": "pending"})
            if any(op[k] != a["p_"+k] for k in ("uid", "amount", "period_start", "period_end")):
                return {"status": "mismatch"}
            if name == "lisan_subscription_begin" or op["status"] == "done":
                return copy.deepcopy(op)
            if self.fail:
                raise OSError("Synthetic allowance outage")
            self.state["subscription"] = op["amount"]
            op.update(status="done", subscription_after=op["amount"])
            return copy.deepcopy(op)
        raise AssertionError("Unexpected money RPC: "+name)


def begin_receipt(name, args, **kwargs):
    if name != "lisan_credit_begin":
        raise AssertionError(name)
    return {"operation_id": args["p_operation_id"], "uid": args["p_uid"], "kind": "debit",
            "amount": args["p_amount"], "status": "pending"}
