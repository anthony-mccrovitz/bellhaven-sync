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
then lowest id. Losers become Inactive with `duplicate_of_account` set.

The reverse pass looks at live Bellhaven children that no website location claimed. If a
live account for the same street exists under a different parent, the facility was sold:
Sandusky → Millstone Care of Sandusky. Otherwise (Alliance, Coldwater) the account is set to
Needs Review with a note rather than guessing a new owner.

**Billing SOP.** Before any re-parent the pipeline checks `lifetime_revenue` and
`outstanding_ar`. Tiffin and Marietta have both, so instead of re-parenting, the proposal
creates a new account under Bellhaven, leaves the old one untouched except for
`chow_current_account`, and adds the website administrator as a contact. Lima and Findlay
have revenue but no AR, so they are re-parented directly. Sandusky, the sold facility, also
has AR, so it keeps its parent and gets `chow_current_account` pointed at the Millstone account.

**Result on this sandbox**: 35 website locations (34 in the directory plus Findlay, which is
only linked from the home page), 31 matched, 25 proposals: 2 CHOW, 4 re-parent, 5 field
fixes, 4 new accounts, 7 duplicates, 1 sold with CHOW link, 2 Needs Review.

## Safe re-runs

Every proposal is fingerprinted on what it would write (kind, account, field values, minus
dated notes). Approved, rejected and applied fingerprints are never re-queued; a pending
proposal that stops being generated is withdrawn; an applied one that comes back raises a
warning. Most approved changes also stop being generated naturally because the CRM now
agrees with the website, and accounts with `duplicate_of_account` or `chow_current_account`
set are excluded from matching as superseded.

## How I used AI tools

I used Claude Code throughout: it pulled the site and the full CRM, and I walked every
website location against the account list by hand first so I had an answer key before any
code existed. The code was generated against that key, with unit tests pinning the trap
cases (Carlisle vs New Carlisle, Amberly Manor in two states, PO Box, zip typo, CHOW vs
direct re-parent, survivor election, idempotent re-runs). The one correction I made along
the way was adding a distinctive-token rule so "Union Square Senior Living" surfaces as a
near miss on the "Bellhaven at Union Square" proposal instead of being invisible. No LLM
calls run inside the pipeline; the matching is deterministic and explainable.

## What I'd build next

- Phone and administrator-contact updates for matched accounts (both are captured and
  currently off by a config flag, because they would add ~30 low-value proposals).
- Mark the Harborview parent account as acquired once all its children are re-linked.
- Snapshot diffing: store each day's scrape so the queue can say "this location appeared
  / disappeared on <date>" rather than only reporting the current state.
- A proper geocoder for addresses instead of token normalisation, and a web-based approval
  flow with auth if more than one reviewer needs it.

## Time spent

(fill in)
