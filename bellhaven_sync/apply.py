"""Write approved proposals to the CRM. Only ever called after a reviewer approves.

After each write the record is read back and every field the proposal set is compared
with what the CRM now holds, so the reviewer sees "landed" per field rather than trusting
the write response. A note is verified by containing the line that was appended.
"""
from __future__ import annotations

from .crm import CRM, CRMError
from .store import Store


def verify(fields: dict, after: dict) -> dict[str, bool]:
    """fields -> {field: landed?} comparing the intended value with the read-back record."""
    out = {}
    for k, v in fields.items():
        got = after.get(k)
        if k == "note":
            last = (v or "").strip().splitlines()[-1] if v else ""
            out[k] = bool(last) and last in (got or "")
        elif isinstance(v, bool):
            out[k] = bool(got) == v
        else:
            out[k] = str(got if got is not None else "") == str(v if v is not None else "")
    return out


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
                results.append({"step": i, "op": act["op"], "ok": True, "account_id": r["account_id"], "after": after,
                                "verified": verify({k: v for k, v in fields.items() if k != "note"}, after)})
            elif act["op"] == "update_account":
                aid = sub(act["account_id"])
                r = crm.update_account(aid, fields)
                after = crm.get_account(aid)
                results.append({"step": i, "op": act["op"], "ok": True, "account_id": aid, "response": r, "after": after,
                                "verified": verify(fields, after)})
            elif act["op"] == "create_contact":
                r = crm.create_contact(fields)
                results.append({"step": i, "op": act["op"], "ok": True, "response": r,
                                "contact_id": r.get("contact_id"), "account_id": fields.get("account_id")})
            elif act["op"] == "update_contact":
                cid = act["contact_id"]
                r = crm.update_contact(cid, fields)
                after = crm.get_contact(cid)
                results.append({"step": i, "op": act["op"], "ok": True, "contact_id": cid, "response": r, "after": after,
                                "verified": verify(fields, after)})
            else:
                raise CRMError(f"unknown op {act['op']}")
        except CRMError as e:
            ok = False
            results.append({"step": i, "op": act["op"], "ok": False, "error": str(e)})
            break  # don't continue a half-applied bundle; the reviewer sees exactly where it stopped

    landed = ok and all(all(v.values()) for s in results for v in [s.get("verified", {})])
    result = {"ok": ok, "landed": landed, "steps": results, "refs": refs}
    store.mark_applied(pid, result, ok)
    return result
