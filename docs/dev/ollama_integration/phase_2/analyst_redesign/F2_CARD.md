# Card F2 — Reality-grounded identifier screening

- Date: 2026-09-25
- Status: implemented, awaiting RA/HI review
- Contract: amendment A3 in `CONTRACT_READ_FIRST.md`; `TECHNICAL_REFERENCE.md` §8.1
- Depends on: F1 (report schema v3 carries the two screening fields)

## Why

The first full-corpus run reported the word `Number` as a passport number 130 times, and
`25925925925925924` as a credit card 224 times. The HI's direction:

> we know there are certain sequences not allowed... that information IS public knowledge.
> we should be working at a level of intelligence that can at least look at these and say
> "that's likely not a valid number, I'll flag it still but rank it lower" or "that's
> definitely not real, I won't even include it"

## Design

`screen_identifier(kind, value, *, labeled)` in `detectors.py` answers two questions that an
earlier draft wrongly merged into one:

| axis | question | values |
|---|---|---|
| `plausibility` | is this a real identifier? | `valid` / `suspect` / `impossible` |
| `subject` | whose is it? | `personal` / `organizational` / `unknown` |

Only `impossible` is refused at scan time. `suspect` demotes one rank and is labelled.
`organizational` is labelled and usually rank-neutral, because contact-kind facts already
rank `low`.

It lives in `detectors.py` rather than a new module because `current_detector_rules()` is
the SHA-256 of that file alone. Splitting the rules out would put them outside the
provenance seal.

## Rules and their public basis

**Impossible** — passport with no digit; every digit the same (card/ssn/routing/account);
card length outside {13,14,15,16,19} or with a leading zero; the three published placeholder
SSNs; a counted run; NANP area or exchange starting 0/1 or being an N11 service code; an
IBAN whose country has no scheme or whose length is wrong for it.

**Suspect** — SSA never-issued SSN ranges (area 000/666/9xx, group 00, serial 0000, with 9xx
group 70-88 named as an ITIN); published test PANs; repeating-decimal float artifacts; card
IINs outside issued ranges; ABA prefixes outside 00-12/21-32/61-72/80; the 555 exchange;
reserved documentation domains; unlabelled dates and unlabelled nine-digit routing
candidates; ages over 120; epoch dates.

**Organizational** — toll-free and premium-rate area codes; role mailboxes.

## Two decisions worth reading

**Rule 25 was narrowed.** The plan proposed demoting any numeric identifier from a
spreadsheet cell. Measurement showed that would demote 186 card hits — but a real payroll
workbook full of real card numbers is exactly the find that matters. The spreadsheet problem
was repeating decimals rendered as floats (`5925925925925926` is 16/27), so the rule now
tests the digits for a short repeated pattern with a rounded tail. Precision without the
blanket penalty.

**A date in the future is suspect, not impossible.** Logically it cannot be a birth date.
But detector output is re-verified byte for byte on resume, so nothing the clock can change
may cause a hit to disappear. Determinism wins.

## Schema v10 — a deviation from the approved plan

The plan said "No change to `analyst_detector_hits` or any DB schema". Two approved rules
broke that: label proximity for `dob` and `routing` needs the surrounding document text,
which exists only during the scan. The report layer holds the hit, not the document.

One additive nullable column, `analyst_detector_hits.labeled` (`NULL`/`0`/`1`). It is the
**only** screening input that cannot be recomputed from `(kind, value)`, which keeps the
rules free to change without a migration. `NULL` means "not recorded" and is never held
against a value, so pre-v10 rows screen exactly as before.

A new detector *kind* was considered and rejected: `kind` is a DDL list interpolated at
import, so adding one would silently rewrite the historical schema of v1-v9 — the trap N1
hit with contact states. Verified: all nine historical snapshot digests are unchanged.

## Consequences accepted

1. **In-flight runs refuse to resume** with `PARSER_DRIFT`. `DETECTOR_RULES_VERSION` is now
   `analyst-detectors-v2`. HI confirmed no run is mid-flight.
2. **Fast mode sends fewer files to the model** — `phase1.py:373` selects on hits.
3. **Host risk levels move.** A host whose only HIGH evidence was a never-issued SSN drops
   to MED. That is the point.
4. **Existing reports do not change.** Seeing this needs a re-run.
5. **A stored hit the new rules would refuse is reported as `suspect`**, not deleted and not
   a crash. Rules change; recorded evidence does not disappear. This was a real bug found by
   the suite, not a hypothetical.

## Measured

| corpus | hits | valid | organizational | suspect | dropped |
|---|---|---|---|---|---|
| gold set (166 docs) | 574 | 151 | 0 | 423 | **0** |
| run `7db3af45` | 4,161 | 2,724 | 839 | 158 | 440 |

Label proximity is not in those figures; it fires only where the text is in hand, so the
1,630 `dob` hits will move further on a re-run.

## Verification

`shared/tests/test_analyst_f2_identifier_rules.py` — 89 tests.

**The gold-set anchor was broken when written, and the fix is the interesting part.** It
screened `scan()` output — but `_scan` now refuses impossible values, so the guard could
never fail. It also pointed at the wrong fixture directory and was finding zero files. It
now reads the fixtures directly with its own expressions, and a companion test asserts the
corpus still yields what the guard assumes (67 SSNs, 36 cards, 152 phones, 168 emails) so it
cannot go vacuous again.

Guards proved by mutation, each reverted:

| mutation | gold values dropped | tests failed |
|---|---|---|
| never-issued SSN areas → impossible | 59 | 2 |
| 555 exchange → impossible | 152 | 3 |
| published test PANs → impossible | 36 | 7 |
| repeating cycle → impossible | 0 (see below) | 4 |

The cycle mutation does **not** trip the gold anchor, because `_screen_card` checks the
published-test-PAN list before the cycle rule, so `4242424242424242` never reaches it. It is
caught instead by `test_a_repeating_decimal_rendered_as_a_float_is_suspect`. Recorded here
because the plan predicted the anchor would catch it, and it does not.

Suites: **3,776 shared+experimental** passed, 1 skipped, only the known pre-existing
`test_daemon_cli.py::test_daemon_modules_import_without_tkinter` failure. **2,126 gui**.

Fixtures updated, all for the same reason — they used values the rules now screen:
`test_analyst_c10b.py` (`123-45-6789` is a published placeholder), `test_analyst_r3c.py`
(`value-0` as an SSN), `test_analyst_c16.py` (v2 downgrade drops the new column),
`test_analyst_r3a.py` / `test_analyst_r4a.py` (version pins).

## Docs

- `docs/ANALYST_GUIDE.md` — new "Known pitfalls and limitations" section, plain language,
  including the ZIP+4 / routing collision and what the marks mean.
- `docs/TECHNICAL_REFERENCE.md` §8.1 — the deep version: checksum strength table, why label
  proximity needs a column, determinism, and the benchmark constraint.

## HI test needed

Re-run `136.50.251.81 Ted and Sabina` against mimir in Full read, synthetic content only.
Expected: no `passport` row reads `Number`, no 17-digit card, toll-free numbers marked
`organizational`, and dates without a nearby label marked `suspect`. Check the narrative is
unchanged — F2 does not touch the reduce prompt.
