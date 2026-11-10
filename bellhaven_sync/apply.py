"""Write approved proposals to the CRM. Only ever called after a reviewer approves."""
from __future__ import annotations

from .crm import CRM, CRMError
from .store import Store


def apply_proposal(store: Store, crm: CRM, pid: int) -> dict:
    p = store.get_proposal(pid)
    if not p:
        raise ValueError(f"proposal {pid} not found")
    if p["status"] != "approved":
        raise ValueError(f"proposal {pid} is {p['status']}, not approved")

    refs: dict[str, str] = {}
    results: list[dict] = []
    ok = True

    def sub(v):
        return refs.get(v, v) if isinstance(v, str) and v.startswith("$") else v

    for i, act in enumerate(p["actions"]):
        fields = {k: sub(v) for k, v in act.get("fields", {}).items()}
        try:
            if act["op"] == "create_account":
                r = crm.create_account(fields)
                refs[act.get("ref", "$new")] = r["account_id"]
                after = crm.get_account(r["account_id"])
                results.append({"step": i, "op": act["op"], "ok": True, "account_id": r["account_id"], "after": after})
            elif act["op"] == "update_account":
                aid = sub(act["account_id"])
                r = crm.update_account(aid, fields)
                after = crm.get_account(aid)
                results.append({"step": i, "op": act["op"], "ok": True, "account_id": aid, "response": r, "after": after})
            elif act["op"] == "create_contact":
                r = crm.create_contact(fields)
                results.append({"step": i, "op": act["op"], "ok": True, "response": r})
            else:
                raise CRMError(f"unknown op {act['op']}")
        except CRMError as e:
            ok = False
            results.append({"step": i, "op": act["op"], "ok": False, "error": str(e)})
            break  # don't continue a half-applied bundle; the reviewer sees exactly where it stopped

    result = {"ok": ok, "steps": results, "refs": refs}
    store.mark_applied(pid, result, ok)
    return result
