# Plan: Card V2-4 — README Entry-Point Clarity + Validation Report Refresh

## Context

V2 of the Reddit OD module (Cards V2-1 through V2-3) is code-complete. Card V2-4 is the final docs/operator-clarity card:
- Make Reddit EXP entry points obvious without rewriting the README
- Refresh `12-V2_VALIDATION_PLAN.md` to reflect V2 implementation state (currently still a blank template)
- Create a minimal V2 validation report to close the loop the same way `07-CARD6_VALIDATION_REPORT.md` did for V1

## Current State

**README.md**
- `Experimental Features > Reddit Ingestion` section (lines 344–348) already carries both strings:
  - `Dashboard → ▶ Start Scan dialog → Reddit Grab (EXP)` 
  - `Dashboard → 📋 Servers window → Reddit Post DB (EXP)`
- Grep validation passes as-is
- Dashboard section (lines 101–112) has **no Reddit reference** — a new user reading top-down won't find the entry points until line 344

**12-V2_VALIDATION_PLAN.md**
- Status label legend is present but no actual PASS/FAIL/PENDING markers have been set
- Needs a state header and per-section status stamps reflecting: V2-1–V2-3 code-complete, validation PENDING

**07-CARD6_VALIDATION_REPORT.md**
- V1-only report; do not append V2 notes here
- Need a new `14-V2_VALIDATION_REPORT.md`

## Files to Touch

| File | Change |
|------|--------|
| `README.md` | Add one bullet to Dashboard section referencing Reddit EXP entry points |
| `docs/dev/reddit_od_module/12-V2_VALIDATION_PLAN.md` | Add V2 implementation-state header + per-section status markers |
| `docs/dev/reddit_od_module/14-V2_VALIDATION_REPORT.md` | Create minimal V2 completion report (new file) |

## Step-by-Step Implementation

### 1. README.md — Dashboard section

Add one bullet to the "From here you can:" list (after the Start Scan bullet):

```
- Reddit ingestion from `r/opendirectories` — `Reddit Grab (EXP)` in the Start Scan dialog, `Reddit Post DB (EXP)` in the Servers window (see [Experimental Features](#experimental-features))
```

This is additive and surgical. Sits naturally next to the Start Scan bullet since that's where Reddit Grab lives. Full detail stays in the Experimental Features section — the Dashboard bullet is just a pointer.

### 2. 12-V2_VALIDATION_PLAN.md — Status refresh

Add a state header directly below the date line:

```markdown
## V2 Implementation State (2026-04-06)

Cards V2-1 through V2-3: code-complete, test-complete, not yet formally validated this session.  
Card V2-4: docs-only (this card).  
Formal automated validation and HI manual gates: PENDING.
```

Add per-section status stamps in the Automated Checks and Manual HI Gate sections:
- Automated sections A–D: `**AUTOMATED: PASS**` — suites executed and passing for V2-1 through V2-3
- Manual Flow A–D: `**MANUAL: PENDING**` — awaiting HI live-session flows
- Exit Criteria: `**OVERALL: PENDING**` — automated passed, manual gates not yet cleared

### 3. 14-V2_VALIDATION_REPORT.md — New file

Modeled after `07-CARD6_VALIDATION_REPORT.md` structure. Include:
- Date, branch
- Cards implemented (V2-1 through V2-4) with one-line summary each
- Files changed per card
- Automated check commands (same as 12-V2_VALIDATION_PLAN.md sections A–D)
- Remaining risks (same `utcnow` deprecation, unofficial endpoint, rate limiting)
- Status summary table: AUTOMATED PENDING / MANUAL PENDING / OVERALL PENDING

## Style Constraints

- Match README voice (casual, direct, no hedging)
- Match existing doc tempo in the `docs/dev/reddit_od_module/` files
- No AI filler phrases ("robust", "seamless", etc.)
- No invented URLs or defaults

## Verification

```bash
# Grep checks (should pass before and after)
grep -n "Reddit Grab (EXP)" README.md
grep -n "Reddit Post DB (EXP)" README.md

# Optional regression sweep
xvfb-run -a ./venv/bin/python -m pytest -q -k "reddit or redseek"
```

Expected grep output: at least 2 hits each (one in Dashboard section after the edit, one in Experimental Features).

## Risks / Notes

- Reddit entry points already present in Experimental Features — no risk of introducing drift
- The Dashboard addition is one bullet, not a section rewrite — safe
- 14-V2_VALIDATION_REPORT.md is new but follows an established pattern
- No production code changes required
