"""Link each website location to CRM accounts.

Matching is tiered and explainable rather than a single fuzzy score, so a reviewer
can see exactly which signals fired:

  Tier A  same street (normalised) AND (same zip OR same city+state)   -> same facility
  Tier B  same name (normalised) AND same city+state AND (same zip OR same phone)
          (covers accounts whose billing street is a PO Box)
  Tier C  "near miss": shown as evidence only, never auto-linked
          (same name but different state, similar name in same city, same phone, ...)

Several live accounts can land in Tier A/B for one location; that is how we find
duplicates (e.g. a pre-acquisition "Harborview ..." record and the current
"Bellhaven ..." record at the same address). The group then elects one survivor.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .normalize import (
    distinctive_overlap, is_po_box, name_similarity, norm_city, norm_name, norm_phone, norm_street, norm_zip,
)
from .scraper import Location


def is_parent_account(a: dict) -> bool:
    return a["name"].endswith("(Parent Account)") or (not a.get("billing_city") and not a.get("billing_street"))


def is_live(a: dict) -> bool:
    """An account that still represents a facility for sales purposes."""
    return (a.get("status") != "Inactive"
            and not a.get("duplicate_of_account")
            and not a.get("chow_current_account"))


@dataclass
class Candidate:
    account: dict
    tier: str                      # A | B | C
    signals: dict = field(default_factory=dict)

    @property
    def account_id(self) -> str:
        return self.account["account_id"]


@dataclass
class MatchResult:
    location: Location
    group: list[Candidate]         # live accounts judged to be this facility (tier A/B)
    near: list[Candidate]          # evidence-only near misses (tier C, or non-live A/B)
    survivor: Candidate | None

    @property
    def losers(self) -> list[Candidate]:
        return [c for c in self.group if self.survivor and c.account_id != self.survivor.account_id]


def signals_for(loc: Location, a: dict) -> dict:
    s = {
        "street_eq": bool(norm_street(loc.street)) and norm_street(loc.street) == norm_street(a.get("billing_street", "")),
        "zip_eq": norm_zip(loc.zip) == norm_zip(a.get("billing_zip", "")),
        "city_eq": norm_city(loc.city) == norm_city(a.get("billing_city", "")),
        "state_eq": (loc.state or "").upper() == (a.get("billing_state") or "").upper(),
        "name_eq": norm_name(loc.name) == norm_name(a.get("name", "")),
        "name_sim": round(name_similarity(loc.name, a.get("name", "")), 3),
        "phone_eq": bool(norm_phone(loc.phone)) and norm_phone(loc.phone) == norm_phone(a.get("phone", "")),
        "token_overlap": distinctive_overlap(loc.name, a.get("name", "")),
        "crm_po_box": is_po_box(a.get("billing_street", "")),
    }
    return s


def tier_for(s: dict) -> str | None:
    if s["street_eq"] and (s["zip_eq"] or (s["city_eq"] and s["state_eq"])):
        return "A"
    if s["name_eq"] and s["city_eq"] and s["state_eq"] and (s["zip_eq"] or s["phone_eq"]):
        return "B"
    if (s["name_eq"]
            or (s["name_sim"] >= 0.8 and (s["city_eq"] or s["zip_eq"]))
            or (s["name_sim"] >= 0.6 and s["city_eq"] and s["state_eq"])
            or (s["token_overlap"] >= 2 and s["city_eq"] and s["state_eq"])
            or s["street_eq"]
            or (s["phone_eq"] and s["state_eq"])):
        return "C"
    return None


def choose_survivor(group: list[Candidate], loc: Location, contacts_by_account: dict[str, list[dict]],
                    parent_id: str) -> Candidate | None:
    """Deterministic election. Billing continuity first, then the record that already
    looks right, then the richer record, then the lowest id for stability."""
    if not group:
        return None

    def key(c: Candidate):
        a = c.account
        cs = contacts_by_account.get(c.account_id, [])
        admin_match = any(
            norm_name(x.get("name", "")) == norm_name(loc.administrator) for x in cs if loc.administrator
        )
        return (
            float(a.get("lifetime_revenue") or 0) > 0,
            a.get("parent_id") == parent_id,
            c.signals.get("name_eq", False),
            admin_match,
            len(cs),
            (a.get("billing_street") or "").strip().lower() == loc.street.strip().lower(),
            c.signals.get("phone_eq", False),
            # lowest id wins: negate by sorting descending on the inverse
            "".join(chr(255 - ord(ch)) for ch in c.account_id),
        )

    return sorted(group, key=key, reverse=True)[0]


def match_locations(locations: list[Location], accounts: list[dict], contacts: list[dict],
                    parent_id: str) -> list[MatchResult]:
    facilities = [a for a in accounts if not is_parent_account(a)]
    contacts_by_account: dict[str, list[dict]] = {}
    for c in contacts:
        contacts_by_account.setdefault(c["account_id"], []).append(c)

    results: list[MatchResult] = []
    for loc in locations:
        group: list[Candidate] = []
        near: list[Candidate] = []
        for a in facilities:
            s = signals_for(loc, a)
            t = tier_for(s)
            if not t:
                continue
            cand = Candidate(account=a, tier=t, signals=s)
            if t in ("A", "B") and is_live(a):
                group.append(cand)
            else:
                near.append(cand)
        survivor = choose_survivor(group, loc, contacts_by_account, parent_id)
        near.sort(key=lambda c: (c.tier, -c.signals["name_sim"]))
        results.append(MatchResult(location=loc, group=group, near=near, survivor=survivor))
    return results


def find_successor(account: dict, accounts: list[dict]) -> dict | None:
    """For a Bellhaven account that vanished from the website: is there a live account
    for the same street under a different parent? (e.g. the facility was sold)."""
    best = None
    for b in accounts:
        if b["account_id"] == account["account_id"] or is_parent_account(b) or not is_live(b):
            continue
        if not b.get("parent_id") or b.get("parent_id") == account.get("parent_id"):
            continue
        if norm_street(account.get("billing_street", "")) and \
           norm_street(account["billing_street"]) == norm_street(b.get("billing_street", "")) and \
           (norm_zip(account.get("billing_zip", "")) == norm_zip(b.get("billing_zip", "")) or
                (norm_city(account.get("billing_city", "")) == norm_city(b.get("billing_city", "")) and
                 (account.get("billing_state") or "").upper() == (b.get("billing_state") or "").upper())):
            best = b
            break
    return best
