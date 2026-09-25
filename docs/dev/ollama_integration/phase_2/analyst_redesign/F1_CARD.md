# Card F1 — Collapse repeated facts

- Date: 2026-09-25
- Status: implemented, awaiting RA/HI review
- Contract: amendment A3 in `CONTRACT_READ_FIRST.md`; `REPORT_JSON_SCHEMA.md` §1.1-1.2
- Depends on: nothing. Independent of F2.

## Why

The first full-corpus run against a remote backend (`7db3af45`, 1,244 files, Full read,
qwen3.8-27b on mimir) produced a good narrative and an unreadable facts table. The HI's report:
"there is a lot of repetition in the display", with four identical SSN rows in the screenshot.

Measured against that run's database:

| | |
|---|---|
| facts in `report.json` | 500 — which is `MAX_REPORT_JSON_FACTS`, not the whole picture |
| distinct on `(kind, value, file)` | **242** |
| detector hits behind it | 4,161 |
| model findings behind it | 1,907 |
| model findings that reached the report | **0** |

The duplicates were not merely ugly. `_load_ranked_facts` emitted one fact per detector
occurrence; rows differing only in `provenance` — a field no surface renders — filled the budget
before any model finding could reach it.

## What changed

- `_load_ranked_facts` folds occurrences on `(source, kind, category, quote, file)`, **before**
  the per-bucket counter and the final truncation. The first occurrence by the existing sort key
  is kept, so `provenance` still names a real span.
- `GroundedFact` gains `occurrences: int = 1` and `plausibility: str = "valid"`. Report schema
  becomes **3**; `SUPPORTED_REPORT_SCHEMA_VERSIONS` becomes `(1, 2, 3)` and the fact key set is
  version-gated like the run key set already was.
- `plausibility` is written but never varied here. It is reserved so the identifier-screening
  card does not need a second schema bump.
- Two shared pure helpers, `fact_seen_label` and `fact_rank_label`, so all five row builders
  agree on how a count and a demoted rank read.
- A `Seen` column on the GUI Treeview, markdown, text, HTML and CSV. The `## FACTS` / `FACTS`
  headings are untouched — `report_export.py` splits rendered output on those literals.

`report.html` and `findings.csv` / `findings.jsonl` **on disk** are unchanged. They stream
`FindingReportRow` pages, are not capped, and remain the full per-occurrence evidence record.

## Decisions

| decision | HI choice |
|---|---|
| grouping key | one row per value **per file** — the File column keeps naming one file |
| visible count | yes, accept the schema bump |
| `plausibility` vocabulary | three values reserved up front (`valid` / `suspect` / `public`) so F2 needs no second bump |

## Deviation from the plan

The plan wrote the demoted rank as `low · suspect` and this card ships exactly that, but the
`plausibility` vocabulary gained a third value (`public`) after measurement showed 830 of 1,312
phone hits were toll-free — real numbers, published business contacts, not personal data.
Calling those "suspect" would be false. F1 reserves the value; F2 decides when it is written.

## Verification

- `shared/tests/test_analyst_f1_fact_collapse.py` — 20 tests
- `gui/tests/test_analyst_f1_facts_table.py` — 5 tests
- Updated for the new shape: `test_analyst_r5.py` (3 pinned headers), `test_analyst_r7.py`
  (row tuple), `test_analyst_n2a_identity.py` (version pin, and the unsupported-version sentinel
  moved from 3 to 4)

**Guard proved.** With `report_state.py` stashed and the tests left in place, three fail, and
`test_the_collapse_happens_before_the_cap` fails with the exact production symptom:

```
assert 2 == 500
  where 500 = len((GroundedFact(kind='ssn', ..., occurrences=1, plausibility='valid'), ...))
```

Suites: **3,680 shared+experimental** passed, 1 skipped, with only the known pre-existing
`experimental/webui/tests/test_daemon_cli.py::test_daemon_modules_import_without_tkinter`
failure. **2,126 gui** passed.

## HI test needed

Existing reports do not change — `report.json` is written once by `finalize_report`. Seeing this
on the real corpus needs a re-run of `136.50.251.81 Ted and Sabina` (synthetic content only).
Expected: the table drops from 500 rows to roughly 242, repeated values carry a count such as
`4x`, and cap slots free up for evidence that never appeared before.

Opening an existing v2 report must still work, with a blank `Seen` column.
