# F-series closeout — report quality and identifier screening

- Date: 2026-09-26
- Status: implemented, awaiting RA/HI review
- Cards: `F1_CARD.md`, `F2_CARD.md`, `F3_CARD.md`, `F4_CARD.md`
- Amendments: A3, A4, A5 in `CONTRACT_READ_FIRST.md`

## Why the series existed

The first full-corpus run against a remote backend (1,244 files, Full read, qwen3.8-27b on
mimir) produced a narrative the HI called exactly what they were looking for, and a facts
table that was close to unusable. Two complaints:

> there is a lot of repetition in the display

> a field labelled SSN populated with 00-00-0000 is obviously a bogus placeholder

Measured against that run's database, both were worse than they looked:

| | |
|---|---|
| facts in `report.json` | 500, which is the cap, not the total |
| distinct on `(kind, value, file)` | 242 |
| detector hits behind the cap | 4,161 |
| model findings behind the cap | 1,907 |
| model findings that reached the report | **0** |
| `passport` rows | 130, across 4 distinct values, every one false |
| `card` rows | 281, of which 224 were 17-digit spreadsheet float artifacts |

## What shipped

| card | what |
|---|---|
| **F1** | a fact is `(source, kind, category, quote, file)`; occurrences fold into one row with a count, **before** the cap. Report schema 3 |
| **F2** | `screen_identifier` grades every detected value against published allocation rules on two axes. Report schema 3, database schema 10, `analyst-detectors-v2` |
| **F3** | an affiliations block derived from grounded contact evidence, never prompted. Report schema 4 |
| **F4** | a run header: start, finish, elapsed, throughput, folders, model, provenance. Report schema 5, database schema 11 |
| — | double-click a completed run to open its report; report dialog padding and height |
| — | export dialog extracted to a satellite; `analyst_tab.py` 1499 → 1295 |

## Verified at closeout

Every number below was read from the code at closeout, not copied from a card.

```
report schema      5, readers accept (1, 2, 3, 4, 5)
database schema    11, all 11 versions known and digest-pinned
detector rules     analyst-detectors-v2
affiliations       threshold 3 files, caps 20 organisations / 25 toll-free
screening tables   40 free-mail domains, 15 birth-date labels, 48-char label window
4242424242424242   suspect        (a test card, not an impossible one)
passport "Number"  impossible     (the 130-row false positive)
800-234-5678       organizational (real, and not personal)
```

## Two axes, not one

The design point worth carrying forward. An early draft had a single `plausibility` field
with `valid` / `suspect` / `public`. Measuring the corpus killed it: **827 of 1,312 phone
hits are toll-free** and 12 emails are role mailboxes. Those are entirely real identifiers.
Recording them as "suspect" would have had the report call a real number fake.

So `plausibility` answers *is this a real identifier* and `subject` answers *whose is it*.
A fact can carry both.

## The constraint that shaped F2

`shared/tests/fixtures/analyst_gold/` is built out of the values these rules screen: every
SSN from a never-issued area, every phone on the 555 fiction exchange, every card a
published test PAN. `scripts/analyst_benchmark/` is frozen and must not be edited.

So never-issued ranges grade **suspect**, never **impossible**. Verified: **0 of 574** gold
values is impossible.

## What went wrong, and what caught it

Recorded because each was a real defect, not a near miss.

| defect | caught by |
|---|---|
| the gold-set anchor screened `scan()` output — which already refuses impossible values — and pointed at the wrong directory, so it passed vacuously | writing a companion test asserting the corpus yields what the guard assumes |
| the report timestamp came from the wall clock, so a resumed finalization produced a different manifest digest | `test_analyst_c12` |
| the second attempt read `updated_at_utc`, which a resume also rewrites | `test_analyst_c12` again |
| a stored hit that the new rules refuse crashed report finalization | `test_analyst_r3c` |
| `_format_shodan_status_with_credits` was **retyped** during extraction, changing `<query credits: 123>` to `<123 credits>` | two dashboard tests |
| a satellite's own import of `ensure_dialog_focus` bypassed the patch seam | `test_analyst_r6b` |

Two of those are the same lesson as the N2b transport extraction: **move code verbatim,
never retype it**, and **resolve patch-sensitive names through the owning module**.

Guards were proved by mutation where the risk warranted it. Making never-issued SSN areas
impossible drops 59 gold values; the 555 exchange drops 152; published test PANs drop 36.
Each was reverted.

## Effect on the corpus

Comparing the run before the series to the run after (`7db3af45` → `a12d4883`, both Full
read on the same host):

| | before | after |
|---|---|---|
| model findings in the report | 0 | **270** |
| `passport` rows reading a bare word | 130 | **0** |
| 17-digit spreadsheet cards | 224 | **0** |
| rows collapsed with a count | — | 92 |
| low-rank facts reaching the report | 0 | 35 |

## Known and recorded, not fixed

- **The fact budget starves the lowest rank.** "Sort by rank, take 500" means a dominant
  rank crowds the rest out; a real run gave 73 HIGH / 427 MED / 0 low. Contact facts are
  in the database and in `findings.csv` but not in `report.json`. A reserved per-rank
  quota is the fix, and it should land before any per-person rollup — the two reasons a
  correspondent is invisible (policy versus budget) must not be confused.
- **The affiliation threshold is not a precision instrument.** At 3 files it keeps a
  guitar-parts vendor and drops a law firm sitting at 2. Held as a named constant.
- **`FREE_MAIL_DOMAINS` is hand-maintained.** A provider not on the list appears as a false
  affiliation. Inferring it was measured and does not separate (gmail 27 distinct local
  parts, hotmail 18, utsa.edu 13).
- **Existing reports do not change.** `report.json` is an artifact, written once and never
  migrated. "Rebuild report.json from the database" is an unscoped follow-on.
- **Backlog** from the screening set: non-US identifiers, credentials and secrets, network
  addresses, document placeholders, and promotion signals. Credentials are the largest
  real exposure gap — an open directory's most valuable content is usually a working key.

## Regression at closeout

| suite | result |
|---|---|
| `shared/tests/` + `experimental/` | 3,841 passed, 1 skipped |
| `gui/tests/` | 2,157 passed |
| `scripts/tests/` (excl. frozen benchmark suites) | 573 passed |
| known pre-existing failure | `test_daemon_cli.py::test_daemon_modules_import_without_tkinter` |

`gui/tests/` is run separately from `shared`+`experimental`: the combined run hits a known
Tk abort, recorded in this repository's notes.

## Outstanding for the HI

- Security review of N3 (`e3fae2e`, `5aba273`, `bd30668`, `5e2c885`, `2df7f3b`).
- Review of the F-series diff, which changes what Analyst reports as fact and moves host
  risk levels.
