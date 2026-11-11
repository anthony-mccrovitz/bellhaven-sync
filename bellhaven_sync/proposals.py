"""Turn match results into reviewable, idempotent proposals.

A proposal is a small bundle of CRM API actions plus the evidence that justifies them.
Nothing here touches the CRM; apply.py does that only after a reviewer approves.

Kinds
  update            fix fields on a matched account (name, zip, street, care type, ...)
  reparent          move a matched account under the Bellhaven parent (no AR outstanding)
  chow              SOP: account has revenue AND outstanding AR -> leave it alone, create a
                    new account under Bellhaven, set chow_current_account on the old one
  create            website location with no CRM account at all
  duplicate         second live account for the same facility -> Inactive + duplicate_of
  divest_review     Bellhaven child no longer on the website, no successor known -> Needs Review
  divest_chow       ...successor account exists under another parent, old account has AR -> chow link
  divest_duplicate  ...successor exists, no AR -> old copy becomes Inactive duplicate of successor
  parent_note       every live child of another operator's parent account is now a Bellhaven
                    facility -> note on that parent (status untouched; billing may still reference it)

Retiring a duplicate also re-points its active contacts at the surviving account, so nobody
is orphaned on an Inactive record.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date

from .config import (CARE_TYPE_MAP, CRM_BASE_URL, MOVE_CONTACTS_FROM_DUPLICATES, NOTE_TAG,
                     PROPOSE_COSMETIC_RENAMES, PROPOSE_PHONE_UPDATES, SOLD_ACCOUNT_STATUS)
from .matcher import MatchResult, find_successor, is_live, is_parent_account
from .normalize import norm_care, norm_name, norm_phone, norm_street, norm_zip
from .scraper import Location

PUBLIC_FIELDS = ["account_id", "name", "parent_id", "parent_name", "billing_street", "billing_city",
                 "billing_state", "billing_zip", "care_type", "status", "phone", "lifetime_revenue",
                 "outstanding_ar", "chow_current_account", "duplicate_of_account", "note"]


@dataclass
class Proposal:
    kind: str
    subject: str                 # account id or website slug
    title: str
    summary: str
    confidence: str              # high | medium | low
    evidence: dict
    actions: list[dict]
    fingerprint: str = ""

    def __post_init__(self):
        if not self.fingerprint:
            self.fingerprint = make_fingerprint(self.kind, self.subject, self.actions)


def make_fingerprint(kind: str, subject: str, actions: list[dict]) -> str:
    """Stable across runs: ignores notes (which carry dates) and 'before' snapshots."""
    def strip(a: dict) -> dict:
        f = {k: v for k, v in a.get("fields", {}).items() if k != "note"}
        return {"op": a["op"], "account_id": a.get("account_id", ""), "contact_id": a.get("contact_id", ""),
                "ref": a.get("ref", ""), "fields": f}
    payload = json.dumps({"kind": kind, "subject": subject, "actions": [strip(a) for a in actions]}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def note_line(text: str) -> str:
    return f"[{NOTE_TAG} {date.today().isoformat()}] {text}"


def append_note(existing: str, text: str) -> str:
    existing = (existing or "").rstrip()
    return (existing + "\n" if existing else "") + note_line(text)


def crm_link(account_id: str, token: str) -> str:
    return f"{CRM_BASE_URL}/crm/{token}/accounts/{account_id}"


def public(a: dict) -> dict:
    return {k: a.get(k) for k in PUBLIC_FIELDS}


_CARE_MAP = {norm_care(k): v for k, v in CARE_TYPE_MAP.items()}


def care_type_for(offering: str) -> str:
    return _CARE_MAP.get(norm_care(offering), "")


def primary_care_type(loc: Location) -> str:
    for o in loc.care_offerings:
        if care_type_for(o):
            return care_type_for(o)
    return ""


def mapped_care_types(loc: Location) -> set[str]:
    return {care_type_for(o) for o in loc.care_offerings if care_type_for(o)}


def new_account_fields(loc: Location, parent_id: str, reason: str) -> dict:
    return {
        "name": loc.name,
        "parent_id": parent_id,
        "billing_street": loc.street,
        "billing_city": loc.city,
        "billing_state": loc.state,
        "billing_zip": loc.zip,
        "care_type": primary_care_type(loc),
        "phone": loc.phone,
        "status": "Active",
        "note": note_line(f"{reason} Source: {loc.url}"),
    }


def contact_action(loc: Location, ref: str = "$new") -> dict | None:
    if not loc.administrator:
        return None
    return {"op": "create_contact", "fields": {"account_id": ref, "name": loc.administrator,
                                               "title": "Administrator", "phone": loc.phone}}


def _dedupe_candidates(near) -> list[dict]:
    return [{"account": public(c.account), "tier": c.tier, "signals": c.signals} for c in near[:6]]


def contacts_by_account(contacts: list[dict] | None) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in contacts or []:
        out.setdefault(c.get("account_id", ""), []).append(c)
    return out


def move_contact_actions(from_account: dict, to_account_id: str, by_account: dict[str, list[dict]]) -> tuple[list[dict], list[dict]]:
    """Re-point every active contact on a retired account at its replacement.
    Returns (actions, contacts) so the evidence can list who moves."""
    if not MOVE_CONTACTS_FROM_DUPLICATES:
        return [], []
    moved = [c for c in by_account.get(from_account["account_id"], []) if c.get("is_active", True)]
    actions = [{"op": "update_contact", "contact_id": c["contact_id"], "fields": {"account_id": to_account_id},
                "before": {"account_id": c.get("account_id")}} for c in moved]
    return actions, [{k: c.get(k) for k in ("contact_id", "name", "title", "email", "phone")} for c in moved]


# --------------------------------------------------------------------------- forward pass

def proposals_for_match(mr: MatchResult, parent_id: str, parent_name: str, token: str,
                        contacts: list[dict] | None = None) -> tuple[list[Proposal], dict | None]:
    """Returns (proposals, match_row). match_row is recorded for the 'matched' report."""
    loc = mr.location
    out: list[Proposal] = []
    by_account = contacts_by_account(contacts)
    base_evidence = {"location": loc.to_dict(), "near_misses": _dedupe_candidates(mr.near)}

    if mr.survivor is None:
        conf = "high" if not mr.near else "medium"
        why = "No CRM account matches this website listing on address, name+city, or phone."
        if mr.near:
            why += " Near misses are listed for the reviewer; none met the match threshold (different street/state)."
        actions = [{"op": "create_account", "ref": "$new",
                    "fields": new_account_fields(loc, parent_id, "Created from Bellhaven website listing (no CRM account found).")}]
        ca = contact_action(loc)
        if ca:
            actions.append(ca)
        out.append(Proposal(
            kind="create", subject=loc.slug,
            title=f"Create account: {loc.name}",
            summary=f"{loc.street}, {loc.city}, {loc.state} {loc.zip} · {', '.join(loc.care_offerings)} · {why}",
            confidence=conf, evidence={**base_evidence, "reason": why}, actions=actions))
        return out, None

    surv = mr.survivor
    a = surv.account
    acct_ev = {"account": public(a), "tier": surv.tier, "signals": surv.signals, "crm_url": crm_link(a["account_id"], token)}
    evidence = {**base_evidence, **acct_ev,
                "group": [{"account": public(c.account), "tier": c.tier, "signals": c.signals} for c in mr.group]}

    needs_reparent = a.get("parent_id") != parent_id
    has_ar = float(a.get("lifetime_revenue") or 0) > 0 and float(a.get("outstanding_ar") or 0) > 0
    match_note_bits = []

    # ---- SOP: change of ownership with billing history -------------------------------
    if needs_reparent and has_ar:
        actions = [
            {"op": "create_account", "ref": "$new",
             "fields": new_account_fields(loc, parent_id,
                                          f"CHOW: replaces {a['account_id']} ({a['name']}, parent {a.get('parent_name') or 'none'}), "
                                          f"which is preserved for billing (revenue {a.get('lifetime_revenue')}, AR {a.get('outstanding_ar')}).")},
            {"op": "update_account", "account_id": a["account_id"], "fields": {"chow_current_account": "$new"},
             "before": {"chow_current_account": a.get("chow_current_account", "")}},
        ]
        ca = contact_action(loc)
        if ca:
            actions.append(ca)
        out.append(Proposal(
            kind="chow", subject=a["account_id"],
            title=f"CHOW: {loc.name} (old parent: {a.get('parent_name') or 'none'})",
            summary=(f"Website lists this facility under Bellhaven but CRM has it under "
                     f"{a.get('parent_name') or 'no parent'}. Account has lifetime revenue {a.get('lifetime_revenue')} "
                     f"and outstanding AR {a.get('outstanding_ar')} > 0, so per billing SOP the old account is left "
                     f"untouched; a new account is created under Bellhaven and chow_current_account is set on the old one."),
            confidence="high" if surv.tier == "A" else "medium",
            evidence={**evidence, "sop": "revenue>0 and outstanding_ar>0 -> create new account + chow link"},
            actions=actions))
        # losers can't be pointed at an account that doesn't exist yet; they are picked up next run
        if mr.losers:
            match_note_bits.append("duplicates deferred until CHOW account exists")
        return out, {"slug": loc.slug, "location": loc.to_dict(), "account_id": a["account_id"], "tier": surv.tier,
                     "signals": surv.signals, "note": "; ".join(["CHOW proposed"] + match_note_bits)}

    # ---- ordinary field fixes on the surviving account ------------------------------
    fields: dict = {}
    before: dict = {}
    reasons: list[str] = []

    if needs_reparent:
        fields["parent_id"] = parent_id
        before["parent_id"] = a.get("parent_id", "")
        reasons.append(f"parent is '{a.get('parent_name') or 'none'}' but the facility is listed on the Bellhaven website"
                       + ("" if float(a.get('lifetime_revenue') or 0) == 0 else " (has revenue history but no outstanding AR, so direct re-parent is allowed)"))

    if norm_name(a["name"]) != norm_name(loc.name):
        fields["name"] = loc.name
        before["name"] = a["name"]
        reasons.append(f"name '{a['name']}' is outdated; website calls it '{loc.name}'")
    elif a["name"] != loc.name:
        if PROPOSE_COSMETIC_RENAMES:
            fields["name"] = loc.name
            before["name"] = a["name"]
            reasons.append("cosmetic name difference")
        else:
            match_note_bits.append(f"cosmetic name variant kept: '{a['name']}'")

    if surv.signals["street_eq"]:
        if norm_zip(a.get("billing_zip", "")) != norm_zip(loc.zip):
            fields["billing_zip"] = loc.zip
            before["billing_zip"] = a.get("billing_zip", "")
            reasons.append(f"zip {a.get('billing_zip')} does not match website zip {loc.zip} (same street)")
        if not surv.signals["city_eq"]:
            fields["billing_city"] = loc.city
            before["billing_city"] = a.get("billing_city", "")
            reasons.append("city differs")
        if not surv.signals["state_eq"]:
            fields["billing_state"] = loc.state
            before["billing_state"] = a.get("billing_state", "")
            reasons.append("state differs")
    else:
        # matched on name+city (tier B); street is different, e.g. a PO Box
        fields["billing_street"] = loc.street
        before["billing_street"] = a.get("billing_street", "")
        kind_of = "a PO Box" if surv.signals["crm_po_box"] else "different"
        reasons.append(f"billing street is {kind_of} ('{a.get('billing_street')}'); website shows '{loc.street}'. "
                       f"Low priority: a PO Box can be a legitimate billing address.")

    mapped = mapped_care_types(loc)
    if mapped and a.get("care_type") not in mapped:
        fields["care_type"] = primary_care_type(loc)
        before["care_type"] = a.get("care_type", "")
        reasons.append(f"care type '{a.get('care_type')}' not among website offerings {sorted(mapped)}")

    if PROPOSE_PHONE_UPDATES and loc.phone and norm_phone(loc.phone) != norm_phone(a.get("phone", "")):
        fields["phone"] = loc.phone
        before["phone"] = a.get("phone", "")
        reasons.append("phone differs from website")

    if fields:
        fields["note"] = append_note(a.get("note", ""), "; ".join(reasons) + f". Source: {loc.url}")
        kind = "reparent" if "parent_id" in fields else "update"
        only_street = set(fields) == {"billing_street", "note"}
        conf = "high" if surv.tier == "A" else ("low" if only_street else "medium")
        out.append(Proposal(
            kind=kind, subject=a["account_id"],
            title=f"{'Re-parent' if kind == 'reparent' else 'Update'}: {a['name']}" + (f" → {loc.name}" if "name" in fields else ""),
            summary="; ".join(reasons),
            confidence=conf, evidence={**evidence, "changes": {k: {"from": before.get(k), "to": v} for k, v in fields.items() if k != "note"}},
            actions=[{"op": "update_account", "account_id": a["account_id"], "fields": fields, "before": before}]))

    # ---- duplicates -----------------------------------------------------------------
    for loser in mr.losers:
        la = loser.account
        lfields = {
            "status": "Inactive",
            "duplicate_of_account": a["account_id"],
            "note": append_note(la.get("note", ""),
                                f"Duplicate of {a['account_id']} ({loc.name}) - same facility at {loc.street}, {loc.city} {loc.state}. "
                                f"Survivor chosen for: {_survivor_reason(surv, loser, parent_id)}. Source: {loc.url}"),
        }
        moves, moved = move_contact_actions(la, a["account_id"], by_account)
        out.append(Proposal(
            kind="duplicate", subject=la["account_id"],
            title=f"Duplicate: {la['name']} → {a['name']}",
            summary=(f"'{la['name']}' (parent: {la.get('parent_name') or 'none'}) and '{a['name']}' (parent: {a.get('parent_name') or 'none'}) "
                     f"are both live accounts for {loc.street}, {loc.city}. Keep {a['account_id']}; mark this one Inactive with duplicate_of_account."
                     + (f" {len(moved)} active contact(s) move to the survivor." if moved else "")),
            confidence="high",
            evidence={**base_evidence, "account": public(la), "tier": loser.tier, "signals": loser.signals,
                      "crm_url": crm_link(la["account_id"], token),
                      "survivor": public(a), "survivor_crm_url": crm_link(a["account_id"], token),
                      "survivor_reason": _survivor_reason(surv, loser, parent_id), "contacts_moved": moved},
            actions=[{"op": "update_account", "account_id": la["account_id"], "fields": lfields,
                      "before": {"status": la.get("status"), "duplicate_of_account": la.get("duplicate_of_account", "")}}] + moves))

    row = {"slug": loc.slug, "location": loc.to_dict(), "account_id": a["account_id"], "tier": surv.tier,
           "signals": surv.signals,
           "note": "; ".join((["changes proposed"] if fields else ["no change needed"]) + match_note_bits)}
    return out, row


def _survivor_reason(surv, loser, parent_id: str) -> str:
    sa, la = surv.account, loser.account
    if float(sa.get("lifetime_revenue") or 0) > 0 and float(la.get("lifetime_revenue") or 0) == 0:
        return "has billing history"
    if sa.get("parent_id") == parent_id and la.get("parent_id") != parent_id:
        return "already linked to the Bellhaven parent"
    if surv.signals.get("name_eq") and not loser.signals.get("name_eq"):
        return "name matches the website"
    return "richer record (contacts / exact address / phone), then lowest id for a stable choice"


# --------------------------------------------------------------------------- reverse pass

def proposals_for_divested(accounts: list[dict], matched_ids: set[str], parent_id: str, parent_name: str,
                           token: str, site_url: str, contacts: list[dict] | None = None) -> list[Proposal]:
    out: list[Proposal] = []
    by_account = contacts_by_account(contacts)
    for a in accounts:
        if is_parent_account(a) or not is_live(a) or a.get("parent_id") != parent_id:
            continue
        if a["account_id"] in matched_ids:
            continue
        succ = find_successor(a, accounts)
        ev = {"account": public(a), "crm_url": crm_link(a["account_id"], token),
              "website_checked": site_url, "successor": public(succ) if succ else None,
              "successor_crm_url": crm_link(succ["account_id"], token) if succ else None}
        has_ar = float(a.get("lifetime_revenue") or 0) > 0 and float(a.get("outstanding_ar") or 0) > 0
        if succ and has_ar:
            fields = {"chow_current_account": succ["account_id"],
                      "note": append_note(a.get("note", ""),
                                          f"No longer listed on the Bellhaven website. Live account for the same address exists under "
                                          f"{succ.get('parent_name')} ({succ['account_id']}). Account has revenue {a.get('lifetime_revenue')} "
                                          f"and outstanding AR {a.get('outstanding_ar')}, so it is preserved for billing and linked via chow_current_account.")}
            before = {"chow_current_account": a.get("chow_current_account", "")}
            if SOLD_ACCOUNT_STATUS and a.get("status") != SOLD_ACCOUNT_STATUS:
                fields["status"] = SOLD_ACCOUNT_STATUS
                before["status"] = a.get("status")
            out.append(Proposal(
                kind="divest_chow", subject=a["account_id"],
                title=f"Sold (CHOW link): {a['name']} → {succ['name']}",
                summary=(f"'{a['name']}' is under Bellhaven in the CRM but not on the website. '{succ['name']}' under "
                         f"{succ.get('parent_name')} shares its street address, so the facility changed hands. It has outstanding AR, "
                         f"so per SOP it is left in place and chow_current_account points at the successor."),
                confidence="high", evidence={**ev, "sop": "revenue>0 and outstanding_ar>0 -> parent untouched, chow link only"
                                             + (f"; status -> {SOLD_ACCOUNT_STATUS}" if SOLD_ACCOUNT_STATUS else "")},
                actions=[{"op": "update_account", "account_id": a["account_id"], "fields": fields, "before": before}]))
        elif succ:
            fields = {"status": "Inactive", "duplicate_of_account": succ["account_id"],
                      "note": append_note(a.get("note", ""),
                                          f"No longer listed on the Bellhaven website; same address is now '{succ['name']}' under "
                                          f"{succ.get('parent_name')} ({succ['account_id']}). No outstanding AR, so this copy is retired.")}
            moves, moved = move_contact_actions(a, succ["account_id"], by_account)
            out.append(Proposal(
                kind="divest_duplicate", subject=a["account_id"],
                title=f"Sold (retire copy): {a['name']} → {succ['name']}",
                summary=(f"'{a['name']}' is under Bellhaven but not on the website; '{succ['name']}' under {succ.get('parent_name')} "
                         f"has the same address and no AR is outstanding here, so mark Inactive with duplicate_of_account."
                         + (f" {len(moved)} active contact(s) move to the successor." if moved else "")),
                confidence="medium", evidence={**ev, "contacts_moved": moved},
                actions=[{"op": "update_account", "account_id": a["account_id"], "fields": fields,
                          "before": {"status": a.get("status"), "duplicate_of_account": a.get("duplicate_of_account", "")}}] + moves))
        else:
            if a.get("status") == "Needs Review" and NOTE_TAG in (a.get("note") or ""):
                continue  # already flagged by a previous approved run
            fields = {"status": "Needs Review",
                      "note": append_note(a.get("note", ""),
                                          "Linked to Bellhaven parent but not listed anywhere on the Bellhaven website. "
                                          "Likely sold or closed; no successor account found at this address. Confirm ownership before outreach.")}
            out.append(Proposal(
                kind="divest_review", subject=a["account_id"],
                title=f"Not on website: {a['name']}",
                summary=(f"'{a['name']}' ({a.get('billing_street')}, {a.get('billing_city')} {a.get('billing_state')}) is a live "
                         f"Bellhaven child in the CRM but does not appear on the website. No other account exists for this address. "
                         f"Flag as Needs Review rather than guessing a new owner."),
                confidence="medium", evidence=ev,
                actions=[{"op": "update_account", "account_id": a["account_id"], "fields": fields,
                          "before": {"status": a.get("status")}}]))
    return out


# --------------------------------------------------------------------------- other operators' parents

def proposals_for_parents(accounts: list[dict], matched_ids: set[str], parent_id: str, token: str,
                          about_url: str) -> list[Proposal]:
    """When every live child of another operator's parent account is a facility on the
    Bellhaven website, that operator has effectively been absorbed. Leave the parent's
    status alone (CHOW-preserved billing accounts may still sit under it) but say so in a note."""
    out: list[Proposal] = []
    live_children: dict[str, list[dict]] = {}
    for a in accounts:
        if is_live(a) and a.get("parent_id") and not is_parent_account(a):
            live_children.setdefault(a["parent_id"], []).append(a)
    for p in accounts:
        if not is_parent_account(p) or p["account_id"] == parent_id:
            continue
        kids = live_children.get(p["account_id"], [])
        if not kids or any(k["account_id"] not in matched_ids for k in kids):
            continue
        if NOTE_TAG in (p.get("note") or "") and "absorbed" in (p.get("note") or ""):
            continue  # already noted by a previous approved run
        names = ", ".join(sorted(k["name"] for k in kids))
        fields = {"note": append_note(p.get("note", ""),
                                      f"Operator absorbed by Bellhaven: all {len(kids)} live child account(s) ({names}) are facilities "
                                      f"listed on the Bellhaven Senior Living website and are being re-linked or retired through the "
                                      f"review queue. Parent left Active because CHOW-preserved billing accounts may still reference it. "
                                      f"Source: {about_url}")}
        out.append(Proposal(
            kind="parent_note", subject=p["account_id"],
            title=f"Operator absorbed: {p['name']}",
            summary=(f"Every live child of '{p['name']}' ({len(kids)}: {names}) appears on the Bellhaven website, so this operator's "
                     f"facilities now belong to Bellhaven. Add a note to the parent; its status is not changed."),
            confidence="medium",
            evidence={"account": public(p), "crm_url": crm_link(p["account_id"], token), "website_checked": about_url,
                      "children": [public(k) for k in kids]},
            actions=[{"op": "update_account", "account_id": p["account_id"], "fields": fields, "before": {}}]))
    return out
