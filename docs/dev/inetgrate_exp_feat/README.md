# Integrate Experimental Features Into Main

Status: Active planning workspace (PA/RA canonical track)

This directory is the canonical planning and execution workspace for integrating promoted feature surfaces into Dirracuda core runtime flows.

Input planning notes in `planning_docs/` are reference material, not contract. Contract docs are the files at this directory root.

## Scope

In scope for this wave:
- Promote SearXNG and Reddit ingestion from "experimental-only" UX to core scan workflow surfaces.
- Replace dashboard "Experimental" positioning with an "Accessories" home for non-core utility modules.
- Consolidate DB entrypoints and legacy sidecar handling around clear operator flows.
- Localize provider-specific settings to provider-owned UI/config surfaces.
- Include WebUI parity/cleanup as part of promotion work (no legacy sidecar DB exposure in WebUI surfaces).
- Add a one-time desktop-driven migration flow for legacy sidecar data promotion into main DB.
- Keep runtime behavior defensive, reversible, and test-backed.

Explicitly out of scope for this wave:
- Censys feature promotion (module is suspended).
- Replacing desktop as canonical runtime entrypoint.
- Broad schema rewrites without staged migrations and compatibility gates.

## Canonical Files

- `SPEC.md` — locked decisions, scope, acceptance criteria.
- `ARCHITECTURE.md` — target integration architecture and data-flow contracts.
- `ROADMAP.md` — phase/card ordering and status tracking.
- `TASK_CARDS.md` — executable one-card tasks for DAs.
- `CLAUDE_PROMPTS.md` — copy-paste DA prompts per card.
- `RISK_REGISTER.md` — active risks and mitigations.
- `LESSONS_LEARNED.md` — carry-forward guardrails and pitfalls.
- `BASELINE_CONTRACTS.md` — frozen current-state contracts before implementation.

## Operating Model

- Role split: HI + PA/RA + DA.
- Delivery cadence: one card at a time.
- Change discipline: smallest safe delta, targeted validation first, broaden only when risk warrants.
- Reporting discipline: exact commands, exact PASS/FAIL, explicit assumptions/risks.
- Commit discipline: no commits unless HI explicitly says `commit`.

## Primary Sources Checked

Local repo:
- `README.md`
- `docs/TECHNICAL_REFERENCE.md`
- `CLAUDE.md`
- `gui/components/unified_scan_dialog.py`
- `gui/components/experimental_features_dialog.py`
- `gui/components/experimental_features/registry.py`
- `gui/dashboard/widget.py`
- `gui/components/dashboard_experimental.py`
- `experimental/se_dork/*`
- `experimental/redseek/*`
- `experimental/webui/*`

External (official):
- Shodan API reference: https://developer.shodan.io/api
- SearXNG Search API: https://docs.searxng.org/dev/search_api.html
- Reddit Data API Terms: https://redditinc.com/policies/data-api-terms
- Reddit Developer Terms: https://redditinc.com/policies/developer-terms
- Censys Platform transition reference (for suspended-module context only): https://docs.censys.com/docs/platform-api-transition-guide
