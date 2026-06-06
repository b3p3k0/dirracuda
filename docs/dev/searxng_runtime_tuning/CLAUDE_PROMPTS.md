# C11 Claude Prompts

Status: Draft only - not yet sent
Last updated: 2026-06-06

PA/RA sends one card prompt at a time. Claude first returns a plan. PA/RA reviews
that plan before authorizing implementation.

## C11A - Planning Prompt

```text
You are the Developing Agent (DA) for Dirracuda C11A. The human and PA/RA will
review your plan before any implementation.

Planning only. Do not edit files, run live network tests, commit, or push.

Read:
- AGENTS.md and repository operating/docs guides
- docs/dev/searxng_runtime_tuning/*
- README.md and docs/TECHNICAL_REFERENCE.md
- current SearXNG models, service, store, probe, tests
- dashboard SearXNG orchestration, provider queue, Running Tasks registry/window

Baseline:
- branch development
- sequential page pipeline commit 662d2a2
- fixed productive-page soft pacing is 10/20/30 seconds

Plan C11A only:
- request timeout default/range 15 / 5-60 seconds
- short retry default/range 30 / 5-60 seconds
- long retry default/range 180 / 60-300 seconds
- service boundary clamps all values
- mature threshold is 5 productive pages or 50 unique URLs
- productive means at least one new unique URL and completed page processing
- early run gets short then long hard retry
- mature run gets short retry only
- valid Retry-After replaces a retry delay slot and remains bounded to 300
- optional threading.Event on run_dork_search, not RunOptions
- interrupt pacing/cooldown waits
- check cancellation at all safe page-stage boundaries
- pass cancellation into probes and stop unnecessary submissions
- explicit cancelled status without schema migration
- preserve completed retained rows and run existing final primary-table sync
- connect Running Tasks and unified provider-queue cancellation
- no error popup and no next-provider launch after cancellation

Important review points:
- RunningTaskRegistry already stores cancel callbacks, but the window currently
  only exposes reopen. Propose the smallest themed UI action.
- Define exact cleanup at insertion, classification, and probe boundaries.
- Do not hold SQLite write transactions during network calls.
- dashboard_scan.py is near 1700 lines; propose a satellite before growth.
- service.py should remain near 1200 lines.

Non-goals:
- Start Scan sliders/layout
- ANSI colorization
- reusable live-test script
- WebUI or Accessories cancellation controls
- schema/auth/dependency changes
- changing productive-page 10/20/30 pacing

Return a decision-complete plan with:
- issue and root cause
- exact status/data/control flow
- interfaces and compatibility
- files and line-count strategy
- cancellation cleanup/failure behavior
- tests and exact commands
- docs changes
- risks and assumptions
```

## Later Cards

C11B, C11C, and C11D prompts will be finalized after the preceding card's
implementation reveals the actual interfaces. Do not send all cards together.

