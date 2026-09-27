# Dorkbook Workspace

Date: 2026-04-19  
Status: Active implementation workspace

This folder is the planning + execution hub for the Dorkbook experimental feature.

Current discussion (2026-09-27): [shared discovery query library](UNIFIED_LIBRARY_PROPOSAL.md)
for Shodan and Self-hosted Search, followed by a broader shipped dork pack.
The [implementation plan](IMPLEMENTATION_PLAN.md) is approved.
U1 storage/migration is implemented; see [validation and recovery](U1_VALIDATION.md).
U2–U6 (application, UI, catalog, closeout) remain pending. The approved dork
selection includes 33A/33B; 33C is deferred in [candidate notes](CANDIDATE_DORKS.md).
HI selected one grouped view and persistent **Apply to Search** behavior.
The [single-view mockup](ASCII_SKETCHES.md#unified-library--review-draft-2026-09-27)
defines the intended UI; current screens still use the original protocol tabs.
The v1 sections below describe the original implementation scope; current
Dorkbook is under Accessories and uses the canonical sidecar path
`~/.dirracuda/data/experimental/dorkbook.db`.

## Canonical Scope (v1)

1. Add `Dorkbook` tab under Experimental dialog.
2. Launch singleton modeless Dorkbook window.
3. Store dorks in sidecar DB (`~/.dirracuda/dorkbook.db`).
4. Protocol tabs: SMB, FTP, HTTP.
5. Built-ins are read-only and italic.
6. Custom rows support add/edit/delete/copy.
7. Search is current-tab only.
8. Delete confirmation supports session-only mute.
9. Persist window geometry and active protocol tab.

## Source of Truth Files

1. `SPEC.md` — behavior and data contracts.
2. `ROADMAP.md` — objective sequence.
3. `TASK_CARDS.md` — card-by-card execution and gates.
4. `ASCII_SKETCHES.md` — mandatory UI contract.
5. `CLAUDE_PROMPTS.md` — prompt templates for implementation/review.
6. `OPEN_QUESTIONS.md` — unresolved decisions (should remain short/empty once locked).
7. `VALIDATION_REPORT.md` — final evidence and PASS/FAIL.
8. `LESSONS_LEARNED.md` — carry-forward implementation guardrails from completed work.
