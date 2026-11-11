# bellhaven-sync

Keeps the CRM's picture of **Bellhaven Senior Living** facilities in step with Bellhaven's
public website: which facilities exist, what they are called, and which parent account they
belong to. Built for the Clipboard Health analyst exercise.

```
website ──scrape──▶ locations ─┐
                               ├─▶ match ─▶ proposals ─▶ review app ─▶ (approve) ─▶ CRM API
CRM API ─fetch──▶ accounts  ───┘                 │
                                                 └─▶ SQLite ledger (decisions, idempotency)
```

Nothing is written to the CRM until a reviewer clicks **Approve** in the review app.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env            # put your CRM token in .env
python -m bellhaven_sync.pipeline --dry-run   # see what would be proposed
python -m bellhaven_sync.pipeline             # queue proposals for review
python app.py                                 # open http://127.0.0.1:5050
python -m unittest discover -s tests          # 20 unit tests
```

## What it does

1. **Scraper** (`bellhaven_sync/scraper.py`) walks the paginated `/communities` directory, every
   community detail page, and the home page (which can announce a community that is not yet in the
   directory; "Bellhaven Meadows of Findlay" is only linked from there). Captures name, street, city,
   state, zip, care offerings, administrator and phone.
2. **Matcher** (`bellhaven_sync/matcher.py`) links each location to CRM accounts using explainable
   tiers rather than one fuzzy score:
   - **Tier A** same normalised street and (zip, or city+state) → same facility
   - **Tier B** same normalised name and city+state and (zip or phone) → covers a PO Box billing address
   - **Tier C** near miss: shown as evidence, never auto-linked (same name in another state, similar
     name in the same city, ...)

   Normalisation collapses `Avenue/Ave`, `West/W`, `Pike/Pk`, `Rehab/Rehabilitation`, `Centre/Center`,
   `&/and`, articles, etc., so "The Arbors at Bellhaven - Dayton" and "Arbors at Bellhaven Dayton" are
   the same record and no rename is proposed for cosmetic differences. "Bellhaven of Carlisle, PA"
   does not match "Bellhaven of New Carlisle, OH"; "Amberly Manor, Hudson OH" does not match the
   Juniper Point "Amberly Manor" in Colorado Springs.

   When several live accounts land in Tier A/B for one location they are the same facility recorded
   twice (typically a pre-acquisition "Harborview ..." or "Cedar Trail ..." record plus the current
   one). A survivor is elected deterministically: billing history first, then the record already under
   Bellhaven, then the one whose name matches, then the richer record, then lowest id.
3. **Proposals** (`bellhaven_sync/proposals.py`) are small bundles of API actions plus evidence:

   | kind | when | CRM change |
   |---|---|---|
   | `update` | matched, field differs (name outdated, zip typo, PO Box street, care type) | PATCH fields + note |
   | `reparent` | matched, wrong parent, **no** outstanding AR | PATCH `parent_id` + note |
   | `chow` | matched, wrong parent, revenue **and** AR > 0 (billing SOP) | leave old account untouched, POST new account under Bellhaven, PATCH `chow_current_account` on old, POST administrator contact |
   | `create` | no account for a website location | POST account + administrator contact |
   | `duplicate` | extra live account for the same facility | PATCH loser `status=Inactive`, `duplicate_of_account=survivor`; PATCH each active contact's `account_id` → survivor |
   | `divest_chow` | Bellhaven child not on website, successor account at same address under another parent, has AR | PATCH `chow_current_account` → successor + note |
   | `divest_duplicate` | same, no AR | PATCH `status=Inactive`, `duplicate_of_account` → successor; contacts → successor |
   | `divest_review` | Bellhaven child not on website, no successor known | PATCH `status=Needs Review` + note |
   | `parent_note` | every live child of another operator's parent account is on the Bellhaven website | PATCH note on that parent (status untouched) |

   Notes are appended, never overwritten, and tagged `[bellhaven-sync <date>]`.
4. **Review app** (`app.py`) shows each proposal with the website record, the CRM record (revenue /
   AR / parent), the match signals that fired, a before→after diff, near misses, and the exact API
   calls. Approve writes immediately through the API, reads every record back and shows a per-field
   "✓ landed" check against what the CRM now holds; Reject records why.
   Rejected items can be re-opened. Links jump to the read-only CRM browser so you can see the change land.
5. **Ledger** (`bellhaven_sync/store.py`, SQLite in `data/sync.db`) records runs, proposals,
   decisions and an audit log.

## Safe re-runs

Every proposal has a fingerprint (kind + subject + the field values it would write, excluding
dated notes). On each run:

- a fingerprint never seen → new pending proposal
- seen and still pending → evidence refreshed in place
- seen and already approved / rejected / applied → skipped, never re-queued
- pending last run but not regenerated now → marked `withdrawn` (the data moved on)
- applied earlier but regenerated now → warning printed (the write did not stick)

Approved changes also change the CRM, so most of them simply stop being generated: a re-parented
account now matches cleanly, a retired duplicate is excluded from matching, an account with
`chow_current_account` set is treated as superseded and the new account matches instead.

## Running daily

- `schedule/crontab.example` — one cron line.
- `.github/workflows/daily-sync.yml` — GitHub Actions at 11:15 UTC; the SQLite ledger is restored
  from and saved to the Actions cache so decisions persist across runs. The CRM token comes from a
  repository secret. The scheduled job only *proposes*; approval still happens in the app.

## Layout

```
bellhaven_sync/
  config.py      env + toggles (cosmetic renames / phone updates are off by default)
  scraper.py     website -> Location
  crm.py         API client (only the 12 mutable fields can be PATCHed)
  normalize.py   street / name / zip / phone normalisation
  matcher.py     tiers, survivor election, successor lookup
  proposals.py   match results -> Proposal(kind, evidence, actions, fingerprint)
  store.py       SQLite ledger
  apply.py       executes an approved proposal's actions, records results
  pipeline.py    CLI entry point
app.py + templates/   Flask review UI
tests/                unit tests for normalisation, matching, SOP and idempotency
schedule/             cron example; .github/workflows for Actions
```
