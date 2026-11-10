"""End-to-end run: scrape -> fetch CRM -> match -> propose -> store.

    python -m bellhaven_sync.pipeline            # normal run, queues proposals for review
    python -m bellhaven_sync.pipeline --dry-run  # print what would be proposed, write nothing
    python -m bellhaven_sync.pipeline --save-snapshot data/snap   # also save raw inputs
    python -m bellhaven_sync.pipeline --snapshot data/snap        # replay saved inputs offline
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import CRM_TOKEN, PARENT_ACCOUNT_NAME, SITE_BASE_URL
from .crm import CRM
from .matcher import match_locations
from .proposals import proposals_for_divested, proposals_for_match
from .scraper import Location, scrape
from .store import Store


def find_parent(accounts: list[dict]) -> dict:
    for a in accounts:
        if a["name"] == PARENT_ACCOUNT_NAME and not a.get("parent_id"):
            return a
    raise SystemExit(f"parent account '{PARENT_ACCOUNT_NAME}' not found in CRM")


def load_inputs(snapshot: str | None, save_snapshot: str | None, crm: CRM):
    if snapshot:
        d = Path(snapshot)
        locations = [Location(**x) for x in json.loads((d / "locations.json").read_text())]
        accounts = json.loads((d / "accounts.json").read_text())
        contacts = json.loads((d / "contacts.json").read_text())
        print(f"replaying snapshot {d}")
        return locations, accounts, contacts
    me = crm.me()
    print(f"CRM: {me.get('message')} ({me.get('candidate')})")
    locations = scrape()
    accounts = crm.list_accounts()
    contacts = crm.list_contacts()
    if save_snapshot:
        d = Path(save_snapshot)
        d.mkdir(parents=True, exist_ok=True)
        (d / "locations.json").write_text(json.dumps([l.to_dict() for l in locations], indent=1))
        (d / "accounts.json").write_text(json.dumps(accounts, indent=1))
        (d / "contacts.json").write_text(json.dumps(contacts, indent=1))
        print(f"saved snapshot to {d}")
    return locations, accounts, contacts


def run(dry_run: bool = False, store: Store | None = None, crm: CRM | None = None,
        snapshot: str | None = None, save_snapshot: str | None = None) -> dict:
    crm = crm or CRM()
    store = store or Store()
    locations, accounts, contacts = load_inputs(snapshot, save_snapshot, crm)
    print(f"{len(locations)} website locations "
          f"({sum(1 for l in locations if l.source != 'directory')} found outside the directory)")
    parent = find_parent(accounts)
    print(f"fetched {len(accounts)} accounts, {len(contacts)} contacts; parent = {parent['account_id']}")

    results = match_locations(locations, accounts, contacts, parent["account_id"])
    proposals, match_rows, matched_ids = [], [], set()
    for mr in results:
        ps, row = proposals_for_match(mr, parent["account_id"], parent["name"], CRM_TOKEN)
        proposals.extend(ps)
        if row:
            match_rows.append(row)
        for c in mr.group:
            matched_ids.add(c.account_id)
    proposals.extend(proposals_for_divested(accounts, matched_ids, parent["account_id"], parent["name"],
                                            CRM_TOKEN, f"{SITE_BASE_URL}/communities"))

    by_kind: dict[str, int] = {}
    for p in proposals:
        by_kind[p.kind] = by_kind.get(p.kind, 0) + 1
    summary = {"locations": len(locations), "accounts": len(accounts), "matched": len(match_rows),
               "proposals": len(proposals), "by_kind": by_kind}

    if dry_run:
        for p in proposals:
            print(f"  [{p.kind:16}] {p.confidence:6} {p.title}")
        print(json.dumps(summary, indent=1))
        return summary

    run_id = store.start_run()
    counts = store.upsert_proposals(run_id, proposals)
    store.replace_matches(run_id, match_rows)
    summary["queue"] = counts
    store.finish_run(run_id, summary)
    print(f"run {run_id}: {len(proposals)} proposals generated -> {counts['new']} new, "
          f"{counts['refreshed']} refreshed, {counts['already_decided']} already decided (skipped), "
          f"{counts['withdrawn']} withdrawn")
    if counts["reappeared_after_apply"]:
        print(f"WARNING: previously applied proposals regenerated (write may not have landed): "
              f"{counts['reappeared_after_apply']}", file=sys.stderr)
    print("by kind:", json.dumps(by_kind))
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--snapshot", help="replay inputs from this directory instead of the network")
    ap.add_argument("--save-snapshot", help="save raw inputs to this directory")
    args = ap.parse_args(argv)
    if not CRM_TOKEN and not args.snapshot:
        raise SystemExit("CRM_TOKEN is not set (copy .env.example to .env)")
    run(dry_run=args.dry_run, snapshot=args.snapshot, save_snapshot=args.save_snapshot)


if __name__ == "__main__":
    main()
