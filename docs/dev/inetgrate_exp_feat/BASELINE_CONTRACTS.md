# Integrate Experimental Features Into Main - BASELINE CONTRACTS

Snapshot date: 2026-05-25
Branch snapshot: `development...origin/development`
Workspace status snapshot: `?? docs/dev/inetgrate_exp_feat/`

## Scope Freeze

- HI direction: Censys is suspended; promotion work excludes Censys.
- This baseline is the pre-implementation contract reference for C1+ cards.

## Key Constraints (Enforced)

1. Desktop runtime entrypoint is `./dirracuda`.
2. `gui/main.py` is compatibility shim only and must remain non-runtime.
3. WebUI runtime entrypoint remains `./venv/bin/python -m experimental.webui.server`.
4. Execute one card at a time with targeted validation and exact PASS/FAIL reporting.
5. No commits unless HI explicitly says `commit`.
6. Fix root causes; do not patch symptoms without identifying source condition.
7. Guard schema/data operations by observed runtime state (table/column presence).
8. Preserve legacy compatibility and sidecar continuity during migration.
9. Check touched file sizes before/after; if any touched production code file exceeds 1700 lines, pause for modularization proposal (tests/docs excluded from hard stop).
10. Preserve behavior outside requested card scope.

## Canonical Entrypoints (Confirmed)

- Desktop app: `./dirracuda`
- Web service: `./venv/bin/python -m experimental.webui.server`

Supporting references:
- `CLAUDE.md` entrypoint rules
- `docs/TECHNICAL_REFERENCE.md` runtime entrypoint sections

## Current UI/Workflow Contracts (Observed)

1. Dashboard top actions are:
   - `▶ Start Scan`
   - `📋 Servers`
   - `🗄 DB Tools`
   - `⚗ Experimental`
   - `⚙ Config`
   - `❔ About`
2. Start Scan is protocol-centric (SMB/FTP/HTTP queueing), not provider-centric.
3. Experimental dialog tab registry currently includes:
   - `SearXNG`
   - `Reddit`
   - `Web UI`
   - `Dorkbook`
   - `Keymaster`
4. SearXNG and Reddit currently run sidecar-backed workflows.
5. Sidecar promotion helpers to main DB already exist (`gui/utils/sidecar_promotion.py`).

## Data/Storage Contracts (Observed)

- Main DB remains canonical for core server records.
- Known sidecars in active use:
  - `~/.dirracuda/data/experimental/se_dork.db`
  - `~/.dirracuda/data/experimental/reddit_od.db`
  - `~/.dirracuda/data/experimental/dorkbook.db`
  - `~/.dirracuda/data/experimental/keymaster.db`
- `docs/TECHNICAL_REFERENCE.md` already marks Censys discovery backend as suspended.

## Validation Conventions (Confirmed)

Common command conventions in-repo:
- `./venv/bin/python -m pytest`
- focused test invocation by module/test path
- `./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick`

Environment checks:
- `xvfb-run` available at `/usr/bin/xvfb-run`

Lint command:
- No single canonical lint command was found in baseline docs; card-level validation should keep using targeted pytest + workflow lane unless HI sets a lint standard.

## File Size Baseline (Likely Touch Zones)

Rubric:
- `<=1200` excellent
- `1201-1500` good
- `1501-1800` acceptable
- `1801-2000` poor
- `>2000` unacceptable

| File | Lines | Grade |
| --- | --- | --- |
| `gui/components/unified_scan_dialog.py` | 1473 | good |
| `gui/dashboard/widget.py` | 1674 | acceptable |
| `gui/components/experimental_features_dialog.py` | 152 | excellent |
| `gui/components/experimental_features/registry.py` | 70 | excellent |
| `gui/components/dashboard_experimental.py` | 194 | excellent |
| `experimental/se_dork/service.py` | 447 | excellent |
| `experimental/redseek/service.py` | 983 | excellent |
| `gui/utils/scan_manager.py` | 1289 | good |
| `gui/utils/sidecar_promotion.py` | 408 | excellent |

## External Reality Checks (Current)

1. Shodan API docs confirm search-credit semantics and that `/shodan/host/count` does not consume query credits.
   - `https://developer.shodan.io/api`
2. SearXNG Search API docs confirm `/search` endpoint support, `format` behavior, and `pageno` pagination parameter.
   - `https://docs.searxng.org/dev/search_api.html`
3. Reddit Data API Terms explicitly prohibit exceeding/circumventing API call limits and enforce policy-based access controls.
   - `https://redditinc.com/policies/data-api-terms`
4. Python subprocess docs warn about `shell=True` and describe safer default non-shell invocation behavior.
   - `https://docs.python.org/3/library/subprocess.html`
5. Python sqlite3 docs show URI modes (including `mode=rw`) and no-implicit-create behavior guidance for strict open semantics.
   - `https://docs.python.org/3/library/sqlite3.html`

## Baseline Assumptions

1. We can promote SearXNG/Reddit to core UI flows without immediate package relocation.
2. Existing sidecar promotion utilities are the safe migration bridge for early cards.
3. Censys remains dormant unless HI issues explicit reactivation.
