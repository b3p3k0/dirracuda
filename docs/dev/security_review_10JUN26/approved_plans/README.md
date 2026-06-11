# Approved Security Review Plans

This directory stores one approved Claude plan per implementation card.

## Approval Contract

1. A fresh Claude instance starts the card as DA and produces a slice-level
   implementation plan without editing files.
2. Codex RA reviews it against `SPEC.md`, `ARCHITECTURE.md`, and the card.
3. HI locks remaining decisions.
4. Claude or Codex saves the approved text here.
5. UI approval authorizes the same Claude DA instance to implement the card
   immediately; no separate handoff prompt or role change is required.

Planning-pack approval does not pre-approve these card plans.

## Naming

```text
<card-id>_<short-slug>.md
```

Examples:

```text
C1_http_redirect_policy.md
C2_http_endpoint_pinning.md
E03_exception_batch_03.md
```

## Required Plan Sections

- Status and approvals
- Objective
- Confirmed root cause
- Non-goals
- Exact behavior and interfaces
- Files expected to change
- Edge and failure cases
- Tests and exact validation commands
- Line-count risk
- Rollback

Once implementation begins, the approved plan is immutable. Any material change
requires a revised plan, a new revision marker, and renewed HI/RA approval.
