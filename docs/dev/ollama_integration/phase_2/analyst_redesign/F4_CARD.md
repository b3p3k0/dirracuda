# Card F4 — The run header

- Date: 2026-09-26
- Status: implemented, awaiting RA/HI review
- Contract: amendment A5 in `CONTRACT_READ_FIRST.md`; `REPORT_JSON_SCHEMA.md` §1.1-1.2
- Schema: report **v5**, database **v11**

## Why

After a sixteen-hour run the HI asked something the report could not answer:

> I would like to know the start/stop/elapsed time of the runs… maybe a header above the
> "what this is" field that shows the date and time ran, we can move the model info line out
> of "what this is" into there, maybe include the input path as well?

All of it existed only in the database and in the output folder's name.

## What it shows

```
Ran         2026-09-25 15:18 → 2026-09-26 07:01 UTC  (15h 43m)  ·  79 files/hour
Source      /home/kevin/testing_docs/136.50.251.81 Ted and Sabina
            1,244 files found  ·  932 read  ·  47 flagged
Output      /home/kevin/.dirracuda/data/experimental/analyst_reports/_analyst/…
Model       qwen3.8-27b (reported by the server, not verified)  ·  Full read
Provenance  run a12d4883  ·  analyst-detectors-v2  ·  report schema 5
```

The model identity and the file counts **move out of `WHAT THIS IS`**, which is now purely
the model's read. Mixing them blurred which half of the page a reader is entitled to trust.

The `Provenance` line earns its place now in a way it would not have a week ago: two reports
on one host can legitimately disagree because the detector rules changed between them, and
nothing else on the page said so.

## Decisions

| decision | HI ruling |
|---|---|
| output folder | in |
| throughput | in |
| which server ran it | out — "the juice aint worth the squeeze" |
| timezone | UTC |

Recording the server would have needed a column on `analyst_runs`: the endpoint is not
stored per run, only `profile_id`, and a profile can be edited afterwards, so resolving it at
display time would be a guess presented as provenance.

## The bug this card is really about

The first implementation took the finish timestamp from the wall clock. `test_analyst_c12`
caught it immediately, and it was a genuine defect rather than a test artifact.

**A finalization that crashes and resumes must rebuild `report.json` byte for byte**, because
its manifest digest is durable and is compared on resume. A clock cannot do that.

The second attempt read `analyst_runs.updated_at_utc`, on the theory that
`begin_finalization` only writes it on the `running → finalizing` transition. That was wrong
too: the resume path puts the run back to `running` and re-enters `begin_finalization`, which
requires `state='running'` and therefore restamps it.

No existing column survives a resumed finalization. So schema **v11** adds
`analyst_runs.report_built_at_utc`, written once with `COALESCE(report_built_at_utc,?)` and
kept. Two tests guard it directly, and `test_analyst_c12` guards it end to end.

A second self-correction inside the same change: the new column first carried
`CHECK(length(...) >= 20)`, which broke two checkpoint tests. No other timestamp column in the
schema has a length check, so it was inconsistent over-constraint and was removed.

## Verification

- `shared/tests/test_analyst_f4_run_header.py` — 26 tests, including elapsed and throughput
  edge cases, a pre-F4 report showing fewer lines rather than blanks, and markdown/HTML
  escaping of `source_root` (operator-typed text that reaches both renderers).
- `gui/tests/test_analyst_f4_run_header_view.py` — 6 tests.
- All ten historical schema digests verified unchanged; v11 recorded in the ladder guardrail.

Updated for the moved lines and the bumps: `test_analyst_n2a_identity_display.py` (the model
identity assertions now read the header — E18 still holds and is still asserted),
`test_analyst_r7.py`, `test_analyst_r5.py`, `test_analyst_c16.py` (the v2 downgrade drops the
new column), `test_analyst_r3a.py` / `test_analyst_r4a.py`, and the version pins.

Suites: **3,837 shared+experimental** passed, 1 skipped, only the known pre-existing
`test_daemon_cli.py` failure. **2,137 gui**. The gui suite aborted once with the known Tk
core-dump flake and was clean on re-run.

## HI test needed

Restart the app first — a running process holds the old modules.

The header appears on any report opened, including older ones, which simply show fewer lines.
The `Ran` line needs `report_built_at_utc`, so the full header only appears on runs finalized
from now on.
