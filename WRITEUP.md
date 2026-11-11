# Writeup

## Matching approach

I treated the street address as the identity of a facility and the name as a strong but
fallible hint. Each website location is compared with every non-parent CRM account on a
handful of normalised signals (street, zip, city, state, name, phone), and a match is one of
three explainable tiers rather than a single fuzzy score:

- **Tier A** same normalised street and (zip or city+state). This catches "4850 Northwest
  Sylvania Avenue" vs "4850 NW Sylvania Ave", and finds the same facility recorded twice
  under different operators (Harborview / Cedar Trail / Bellhaven).
- **Tier B** same normalised name and city+state, plus zip or phone. This is how the Ashtabula
  account with a PO Box billing address still matches.
- **Tier C** near miss, shown to the reviewer but never linked: "Amberly Manor" in Colorado
  Springs vs the Hudson, OH one; "Union Square Senior Living" at a different street;
  "Bellhaven of New Carlisle, OH" vs "Bellhaven of Carlisle, PA".

Name normalisation collapses cosmetic variants ("Rehab and" / "Rehabilitation &",
"Centre"/"Center", "The ... - Dayton") so no rename is proposed for those, while genuinely
outdated names (Riverbend Manor → Bellhaven of Chagrin Falls, Sunny Acres → Bellhaven
Willow Creek, Chesterton Senior Commons → Bellhaven of Chesterton) are.

When several live accounts match one location, a survivor is elected deterministically:
billing history, then already under Bellhaven, then matching name, then the richer record,
then lowest id. Losers become Inactive with `duplicate_of_account` set, and any active
contacts on the loser are re-pointed at the survivor so nobody is stranded on a dead record
(the Owosso duplicate carries an Admissions Director).

The reverse pass looks at live Bellhaven children that no website location claimed. If a
live account for the same street exists under a different parent, the facility was sold:
Sandusky → Millstone Care of Sandusky. Otherwise (Alliance, Coldwater) the account is set to
Needs Review rather than Inactive: the facility most likely still operates under an owner the
CRM doesn't know, so it should stay visible to a human, not disappear. A final pass notes the
other operators' parent accounts (Harborview, Cedar Trail) once every live child of theirs is a
Bellhaven facility; their status is left alone because CHOW-preserved billing accounts still
sit under them.

**Billing SOP.** Before any re-parent the pipeline checks `lifetime_revenue` and
`outstanding_ar`. Tiffin and Marietta have both, so instead of re-parenting, the proposal
creates a new account under Bellhaven, leaves the old one untouched except for
`chow_current_account`, and adds the website administrator as a contact. Lima and Findlay
have revenue but no AR, so they are re-parented directly. Sandusky, the sold facility, also
has AR, so it keeps its parent and gets `chow_current_account` pointed at the Millstone account.
I chose to leave its status Active, reading "leave it exactly as is" literally, so billing
records are never touched beyond the link; the pipeline excludes any account with a
`chow_current_account` from matching, and a one-line config toggle (`SOLD_ACCOUNT_STATUS`)
flips it to Needs Review if the CRM team would rather it drop off active lists.

**Result on this sandbox**: 35 website locations (34 in the directory plus Findlay, which is
only linked from the home page), 31 matched, 27 proposals: 2 CHOW, 4 re-parent, 5 field
fixes, 4 new accounts, 7 duplicates, 1 sold with CHOW link, 2 Needs Review, 2 operator notes.
I approved all but one (the low-confidence Ashtabula PO Box street change, since a PO Box can
be a legitimate billing address). Re-running the pipeline afterwards produced 0 new proposals
(the rejected Ashtabula fix is regenerated and skipped as already decided). End state: the
Bellhaven parent has exactly 35 live Active children, one per website location.

## Safe re-runs

Every proposal is fingerprinted on what it would write (kind, account, field values, minus
dated notes). Approved, rejected and applied fingerprints are never re-queued; a pending
proposal that stops being generated is withdrawn; an applied one that comes back raises a
warning. Most approved changes also stop being generated naturally because the CRM now
agrees with the website, and accounts with `duplicate_of_account` or `chow_current_account`
set are excluded from matching as superseded. After every approval the record is read back and
each field is compared with what was sent, so the reviewer sees "landed" per field rather than
trusting the write response.

## How I used AI tools

I used Claude Code throughout: it pulled the site and the full CRM, and I walked every
website location against the account list by hand first so I had an answer key before any
code existed. The code was generated against that key, with unit tests pinning the trap
cases (Carlisle vs New Carlisle, Amberly Manor in two states, PO Box, zip typo, CHOW vs
direct re-parent, survivor election, idempotent re-runs). The one correction I made along
the way was adding a distinctive-token rule so "Union Square Senior Living" surfaces as a
near miss on the "Bellhaven at Union Square" proposal instead of being invisible. No LLM
calls run inside the pipeline; the matching is deterministic and explainable.

Two honest footnotes. While probing what `POST /accounts` accepts, a probe request created a
junk account (`001AB7308A3522D3AE`). And a test approval of the Tiffin CHOW before the demo
recording created a first copy of the new Tiffin account (`001DF0AB4A1FB3F828`); I reverted
the chow link and re-ran. There is no delete endpoint, so both records are renamed "[VOID] ...",
set Inactive, with a note saying why.

## What I'd build next

- Phone and administrator-contact updates for matched accounts (both are captured and
  currently off by a config flag, because they would add ~30 low-value proposals).
- Pull the acquisition dates from the About page ("Harborview 2025, select Cedar Trail
  communities 2026") into the operator notes and the CHOW notes as evidence.
- Snapshot diffing: store each day's scrape so the queue can say "this location appeared
  / disappeared on <date>" rather than only reporting the current state.
- A proper geocoder for addresses instead of token normalisation, and a web-based approval
  flow with auth if more than one reviewer needs it.

## Time spent

About 8 hours across two days: roughly 2 exploring the site and CRM and building the answer key by hand, 3 directing the build and checking its output, 1.5 fixing what came up (Union Square near miss, care-type bug, API outage, voided records), and 1.5 reviewing, recording, and writing this up.
