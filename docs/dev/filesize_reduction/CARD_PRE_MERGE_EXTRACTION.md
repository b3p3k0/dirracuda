# Card — Pre-merge extraction of near-gate files

- Date: 2026-09-26
- Status: planned, not started
- Trigger: HI asked for a file-size audit before folding the Analyst feature line
  into `development`, so the branch arrives with room to grow.
- Prior art: `LESSONS_LEARNED.md` in this directory (2026-04-21 effort).

## What the limit actually is

Two different numbers are in play, and conflating them caused a wrong statement to the
HI earlier today.

| limit | source | applies to |
|---|---|---|
| **1700 lines** | `docs/dev/ollama_integration/CONTRACT.md` — *"if it is already above 1700 lines or the change would push it above 1700, pause and propose modularization"* | the repository gate |
| **1500 lines** | `phase_2/remote_backends/N1_CARD.md` | a tighter budget set for `analyst_tab.py` and `db_schema.py` only |
| 1200 lines | `phase_1/TASK_CARD_C1.md`, Sherlock C-series | per-card budgets in those lines |

## Audit (production code; tests and the frozen `scripts/analyst_benchmark/` excluded)

| lines | file | headroom to 1700 | action |
|---|---|---|---|
| 1688 | `gui/dashboard/widget.py` | **12** | **Unit B** |
| 1687 | `experimental/webui/db.py` | **13** | **Unit A** |
| 1625 | `gui/components/dashboard_batch_ops.py` | 75 | watch |
| 1593 | `gui/components/app_config_dialog.py` | 107 | watch |
| 1522 | `gui/utils/database_access_write_methods.py` | 178 | none |
| 1501 | `experimental/webui/app.py` | 199 | none |
| 1500 | `experimental/analyst/db_schema.py` | 200 | **deferred, see below** |
| 1295 | `gui/components/experimental_features/analyst_tab.py` | 405 | done (`6519954`) |

The two at the top have effectively no headroom: a single added method trips the gate.

---

## Unit A — `experimental/webui/db.py` (1687)

One module holds two unrelated jobs: building the paginated results tables, and
assembling a single host's full detail payload. They share only six small helpers.

**New `experimental/webui/db_common.py`** (~60 lines) — the helpers both jobs use, so
neither new module has to import the other:

`_connect`, `_inspect_tables`, `_inspect_columns`, `_table_has_columns`,
`_format_last_seen`, `_to_int`, plus `_DEFAULT_DB_PATH`.

**New `experimental/webui/db_details.py`** (~840 lines) — everything that answers "tell me
about this one host":

| function | lines |
|---|---|
| `_get_http_detail` | 201 |
| `_get_smb_detail` | 194 |
| `_get_ftp_detail` | 183 |
| `_probe_tree_lines` | 64 |
| `get_result_details` | 39 |
| `_build_snapshot_tree` | 31 |
| `_latest_snapshot_id_for_host` | 25 |
| `_get_http_latest_access` | 25 |
| `_get_ftp_latest_access` | 23 |
| `_load_probe_snapshot_payload` | 23 |
| `_render_snapshot_tree` | 11 |
| `_format_root_file_name` | 10 |
| `_safe_list`, `_first_non_empty`, `_country_display`, `_clean_text`, `_normalize_snapshot_path` | 30 |

**Stays in `db.py`** (~790 lines): `get_smb_results`, `get_ftp_results`,
`get_http_results`, `get_results_table_rows`, `_build_smb_arm`, `_build_ftp_arm`,
`_build_http_arm`, `_validate_bounds`, the emoji helpers, `export_db`,
`get_sidecar_migration_status`, and the module constants.

### The constraint that shapes it

`app.py:1000` calls `_db.get_result_details(db_path, host, server_id)`, where `_db` is
`experimental.webui.db`. **That name must keep resolving there.** So `db.py` re-exports it:

```python
from experimental.webui.db_details import get_result_details  # re-export
```

This is why the shared helpers go to a third module rather than staying in `db.py`: a
re-export plus `db_details` importing helpers back from `db.py` would be an import cycle.

`sherlock_view.py:20` also imports from `experimental.webui.db`; check its import list is
unaffected before committing. No test patches `get_result_details` or any detail helper
today, so the patch surface is clean — verify that is still true at implementation time.

---

## Unit B — `gui/dashboard/widget.py` (1688)

Two self-contained features, both loosely coupled to the widget. Measured: the Shodan
cluster touches 4 instance attributes and calls exactly **one** method outside itself
(`_resolve_active_config_path`, which stays on the widget and is reached as
`dash._resolve_active_config_path()`). The About cluster calls **nothing** outside itself.

**New `gui/components/dashboard_shodan.py`** (~250 lines):

| method | lines |
|---|---|
| `_prompt_for_shodan_api_key` | 104 |
| `_persist_shodan_api_key_to_config` | 40 |
| `_read_shodan_api_key_from_config` | 26 |
| `_fetch_shodan_query_credits` | 26 |
| `_refresh_shodan_status_display` | 12 |
| `_run_shodan_balance_refresh_worker` | 11 |
| `_ensure_shodan_api_key_for_scan` | 9 |
| `_start_shodan_balance_refresh` | 8 |
| `_finish_shodan_balance_refresh` | 8 |

**New `gui/components/dashboard_about.py`** (~85 lines): `_open_about_dialog` (71),
`_open_user_manual_from_about` (8).

Result: `widget.py` ≈ **1380**.

Location and shape follow `CLAUDE.md`: satellites live in `gui/components/` as
`dashboard_*.py`, each function takes the dashboard instance as `dash`, and messagebox
calls route through `_mb()`.

### The constraint that shapes it

`gui/components/dashboard.py` is a compatibility shim whose docstring is explicit: it
*"keeps all patch-sensitive names bound at module scope so frozen test patch paths
(`gui.components.dashboard.*`) remain valid."*

Extraction must not break that. This is lesson **2** in `LESSONS_LEARNED.md`, and the
Analyst export extraction hit it for real three hours ago: the moved dialog called
`ensure_dialog_focus` through its own module-level import, and a test patching that name on
the tab namespace silently went to "called 0 times". **Resolve every patch-sensitive name
through the owning module at call time, not by importing it into the satellite.**

Candidates to check before moving: `messagebox`, `webbrowser`, `threading`, `filedialog`,
and anything else bound at module scope in `dashboard.py`.

---

## Deferred — `experimental/analyst/db_schema.py` (1500)

At its N1 budget but **200 lines under the repository gate**. The natural split is the DDL
literals into a sibling, which is safe digest-wise because `_expected_snapshot` hashes DDL
*content*, not file layout — all eleven historical digests are computed by executing the
statements.

**Do not do it speculatively.** It is the schema ladder for every user database, and the
risk of touching it is out of proportion to 200 lines of headroom. The trigger is the next
schema version: if adding v12 would push the file past 1700, split first, in its own card,
with the digest guardrail green before and after.

## Not doing

`dashboard_batch_ops.py` (75 lines of headroom) and `app_config_dialog.py` (107) are on the
watch list, not this card. Neither is in the Analyst feature line and neither is being
edited; extracting them now would be churn ahead of a merge rather than clearing a blocker.

---

## Order and discipline

`LESSONS_LEARNED.md` lesson **5** — *"Keep one oversized target per issue/card"* — applies.
The two units are independent and touch no common file, so they are separately committable:
**finish and verify Unit A before starting Unit B.**

Also carried forward from that list: `from __future__ import annotations` in every extracted
module (lesson 3), and no import-time evaluation of defaults (lesson 4).

## Verification, per unit

```bash
# Unit A
./venv/bin/python -m pytest experimental/webui/ -q
./venv/bin/python -m pytest shared/tests/ experimental/ -q

# Unit B
xvfb-run -a ./venv/bin/python -m pytest gui/tests/ -q

# both: the evidence check from lesson 8
git ls-files '*.py' | xargs wc -l | sort -rn | head -20
```

Baseline to match: **3,837 shared+experimental** (plus the known pre-existing
`test_daemon_cli.py::test_daemon_modules_import_without_tkinter` failure) and **2,152 gui**.

Each unit adds a discipline test in the shape of the one written for the Analyst export
satellite (`gui/tests/test_analyst_r6b.py`): the satellite must not import its owner at
module level, and a patch on the owner's namespace must reach the satellite.

### Acceptance

- No production file above 1700 lines.
- `gui/dashboard/widget.py` and `experimental/webui/db.py` both under 1500.
- `experimental.webui.db.get_result_details` still resolves for `app.py`.
- `gui.components.dashboard.*` patch paths still valid.
- Before/after line counts recorded in the closing commit (lesson 8).
