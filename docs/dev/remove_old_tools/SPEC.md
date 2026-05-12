# Specification: Pry/RCE Sunset

## Problem Statement

Pry and RCE subsystems are legacy SMB-auditing features that are out of scope for current Dirracuda direction. They remain in codepaths via hidden or suspended controls, which increases maintenance burden and attack surface.

## Goals

1. Remove Pry and RCE runtime/UI/CLI functionality from active product behavior.
2. Preserve compatibility for existing user databases and historical records.
3. Keep non-Pry/RCE workflows (SMB/FTP/HTTP scan, probe, extract, reporting) unchanged.
4. Produce auditable evidence that Pry/RCE controls and entrypoints are absent after sunset.

## Non-Goals

1. No destructive schema migration or table/column drops.
2. No historical docs scrub across all archives under `docs/dev/*`.
3. No broad architecture refactor unrelated to Pry/RCE removal.
4. No behavior changes to unrelated scanning protocols or UI flows.

## Locked Decisions

1. Schema strategy: runtime sunset (no schema drops now).
2. Legacy data policy: preserve existing historical rows.
3. Docs scope: update only `README.md` and `docs/TECHNICAL_REFERENCE.md` plus `docs/dev/remove_old_tools/*`.

## Scope: Required Removals

1. Hidden unlock/session gates tied to Pry/RCE (`--1337`, `_pry_unlocked`, `_rce_unlocked`).
2. Pry runtime flow in server list and associated dialogs/actions/job routing/status presentation.
3. RCE runtime flow from CLI flag and workflow plumbing through probe/analyzer/status UI.
4. Active config/default keys used exclusively by Pry/RCE runtime behavior.
5. Pry/RCE references in selected user + technical docs.

## Compatibility Boundaries

1. Existing DBs must open and run without migration failures.
2. Legacy tables/columns may remain physically present.
3. Application must stop creating/updating Pry/RCE runtime records going forward.
4. Any compatibility handling must be guarded by runtime schema inspection where needed.

## Interface Contract Changes

1. CLI:
- `--check-rce` is removed from supported options.

2. GUI:
- No Pry actions, dialogs, status columns, or queue controls.
- No RCE controls, toggles, note surfaces, or status columns related to active operations.

3. Runtime:
- No hidden unlock mode to re-enable Pry/RCE codepaths.

4. Config:
- Remove active defaults or accessors exclusively tied to Pry/RCE runtime execution.

## Acceptance Criteria

1. `rg` guardrail checks return no active Pry/RCE runtime references in targeted app paths (allowing explicit legacy-doc references only where intentionally retained outside selected docs).
2. Targeted compile and pytest suites pass for touched components.
3. Scenario matrix reflects sunset state and verifies unchanged core non-Pry/RCE workflows.
4. `README.md` and `docs/TECHNICAL_REFERENCE.md` are updated to final sunset behavior.
5. Card-by-card reports include exact commands and PASS/FAIL evidence.

## Security Rationale

1. Reduce attack surface by removing unused feature interfaces (OWASP Attack Surface Analysis).
2. Reduce secret-handling exposure from dead capability paths (OWASP Secrets Management).
3. Align with secure-by-design and removal of high-risk bad practices (CISA/FBI guidance).
4. Enforce least functionality by disabling/removing unnecessary functions (NIST SP 800-171r3).

## Open Follow-Ups (Out Of Scope For This Project)

1. Optional future archive policy for historical docs beyond selected docs.
2. Optional future DB cleanup utility for users who request purge of legacy Pry/RCE artifacts.
