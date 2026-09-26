# Card F3 — Affiliations

- Date: 2026-09-25
- Status: implemented, awaiting RA/HI review
- Contract: amendment A4 in `CONTRACT_READ_FIRST.md`; `REPORT_JSON_SCHEMA.md` §1.1-1.2
- Depends on: F1 (the fact budget), F2 (`screen_identifier` supplies `organizational`)

## Why

The HI, on the toll-free numbers and role mailboxes F2 marks `organizational`:

> they can help in building a profile (ie who do they seem to do a lot of interaction
> with). I think I feel like it should be used in the analysis, but marked separately

The evidence already existed and was invisible. Contact facts rank `low`, and the 500-fact
budget is spent before the sort reaches them: run `7db3af45` put **zero** email addresses
into `report.json`, out of 399 detector hits. The only place an address surfaced was
`findings.csv`.

## What it does

`_load_affiliations` reads the hit table directly — never the ranked facts, which would
return nothing — and rolls email hits up by domain.

The ranking signal is **distinct files**, not occurrences. That single choice does the
filtering: in the real corpus 63 of 77 candidate domains appeared in exactly one file, all
of them product manuals and receipts sitting in a downloads folder. What survives on the
HI's own host:

| files | domain | |
|---|---|---|
| 19 | `utsa.edu` | UT San Antonio |
| 8 | `neisd.net` | North East ISD |
| 6 | `dfps.state.tx.us` | TX Family & Protective Services |
| 4 | `nisd.net` | Northside ISD |
| 4 | `seymourduncan.com` | guitar parts — see below |
| 3 | `energiseforlife.com` | |

## Decisions

| decision | HI ruling |
|---|---|
| organisations only, no named people | "orgs first, keep the targets small and multiple — better 100 rabbits than 1 moose" |
| threshold | 3 distinct files: "two is a coincidence, three is a pattern" |
| toll-free | collect with a source note, revisit properly later |
| free-mail list | carry it, once the alternative was shown not to work |
| schema pace | "schema changes are ok to be fast and as needed here" |

**The threshold is not a precision instrument, and the card should say so.** At three it
keeps `seymourduncan.com` (4 guitar manuals) and drops `weisslawsatx.com` (a law firm at
exactly 2). It is a named constant, `MIN_AFFILIATION_FILES`, so it is a one-line change once
more hosts have been run.

**The free-mail list is a maintained artifact.** Without it `hotmail.com` tops the list with
83 files and buries everything real. Inferring it from distinct local parts per domain was
measured and does not separate: gmail 27, hotmail 18, **utsa.edu 13**. A domain not on the
list will appear as a false affiliation.

**Toll-free numbers were nearly cut, and the HI was right to keep them.** They do carry
institutional meaning — `800-772-1213` is the SSA's main line, found on a 4506-T;
`800-318-2596` is HealthCare.gov. There were 159 distinct numbers in one run and no offline
way to resolve one to an institution, so they are collected with a file count and one
example source, capped at 25 with an honest total, labelled *collected, not analysed*.

## Noted for later — the sidebar the HI asked to record

**Individual correspondents are invisible in the report, and not because we excluded them.**
Contact facts rank `low`, and the cap is "sort by rank, take 500" — whichever rank dominates
starves the rest. Measured on run `7db3af45` after F1 and F2: 73 HIGH, 427 MED, **0 low**.

So `joe@gmail.com` appearing in 12 files does not reach `report.json` or the GUI facts table.
It is only in `findings.csv`. That is a budget artifact, not a policy decision, and the two
should not be confused when the people rollup is designed.

The clean fix is a **reserved quota per rank** so `low` is never starved — a change to the
cap policy, with no bearing on whether third parties get named. It should land before, or
alongside, any per-person rollup. Its own card.

## Verification

- `shared/tests/test_analyst_f3_affiliations.py` — 29 tests
- `gui/tests/test_analyst_f3_affiliations_view.py` — 5 tests
- Contract boundary refuses a non-canonical order, a mailbox provider, and an
  under-threshold entry, so an invalid block cannot be written by any path.

Two test bugs were found and fixed during the run, both mine: an assertion looking for
"not appear in enough files" against a string reading "no organization appears in enough
files", and a pre-v4 fixture that dropped the block while leaving the version at 4.

Updated for the new top-level key: `test_analyst_r1.py` (exact report shape),
`test_analyst_f1_fact_collapse.py` and `test_analyst_n2a_identity.py` (version pins, and the
unsupported-version sentinel moved from 4 to 5).

## HI test needed

Re-run a host. The block is written at finalization, so existing reports do not gain one —
`report.json` is an artifact, written once, never migrated. (The *database* migrates itself
on open; this card needed no DB change at all.)

If refreshing old runs matters, "rebuild report.json from the database" is a separate card.
