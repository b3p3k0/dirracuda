# Security Review 10 June 2026

| Field | Value |
|---|---|
| Status | PA planning pack complete; awaiting HI approval |
| Owner | VanDelay Security Group |
| HI | Kevin |
| PA/RA | Codex |
| DA | Claude |
| Branch | `development` |
| Baseline commit | `4320614` |

## Purpose

This workspace converts `INITIAL_REPORT.md`, Codex verification, and the
assessing agent's follow-up into a decision-complete remediation program.

The current phase is planning only. No product code may be changed until:

1. Every planning document is present and internally consistent.
2. HI approves the completed planning pack.
3. Codex explicitly transitions from PA to RA.
4. Claude produces a plan for the first implementation card.
5. HI and RA approve that card plan.

Approval of this planning pack does not authorize product-code implementation.

## Roles

| Role | Owner | Responsibility |
|---|---|---|
| HI | Kevin | Locks product decisions, approves plans, performs requested manual tests, and authorizes commits |
| PA | Codex | Produces this planning pack only |
| RA | Codex | Supervises one card at a time, reviews Claude plans and diffs, reruns validation, and accepts or rejects work |
| DA | Claude | Plans or implements exactly one named card in separate sessions |
| AA | External assessing agent | Supplied the initial findings and reconciled technical disagreements |

## Locked Workflow

1. RA refreshes the baseline using C0.
2. Claude runs a plan-only session for one card.
3. RA and HI review the plan.
4. The approved plan is saved under `approved_plans/`.
5. A separate Claude DA session implements only that approved plan.
6. RA reviews the diff and reruns the declared validation.
7. HI performs any required manual test.
8. RA records completion evidence in `EXECUTION_TRACKER.md`.
9. No commit is created unless HI says exactly `commit`.
10. No push is attempted.

## Baseline

Planning verification on 2026-06-10 confirmed:

- Branch: `development`
- Commit: `4320614`
- Worktree: clean except for untracked `docs/dev/security_review_10JUN26/`
- Canonical GUI entrypoint: `./dirracuda`
- Targeted security-adjacent baseline: 95 tests passed
- Pass-only product exception handlers: 448 across 103 files
- Current exception distribution: 375 GUI, 33 experimental, 29 shared,
  6 commands, and 5 tools

The 95-test baseline comprises:

```text
29 HTTP verifier/browser/extract tests
41 SMB extract/quarantine tests
25 import/FTP/viewer tests
```

## Workspace Index

- `INITIAL_REPORT.md` - original assessing-agent report, retained unchanged.
- `SOP_CONSTRAINTS.md` - operating rules and approval boundaries.
- `FINDINGS_RECONCILIATION.md` - final finding dispositions and evidence.
- `SPEC.md` - behavior and acceptance contract.
- `ARCHITECTURE.md` - shared HTTP transport and TLS policy design.
- `TASK_CARDS.md` - sequential execution backlog.
- `VALIDATION_PLAN.md` - automated and manual validation requirements.
- `RISK_REGISTER.md` - active and deferred risks.
- `EXCEPTION_AUDIT_PLAN.md` - 448-handler audit and remediation program.
- `CLAUDE_PROMPTS.md` - copy/paste prompts for supervised Claude sessions.
- `RA_RUNBOOK.md` - Codex review and card-close procedure.
- `EXECUTION_TRACKER.md` - status and evidence ledger.
- `LESSONS_LEARNED.md` - carry-forward guardrails.
- `approved_plans/` - HI/RA-approved Claude card plans.

## Locked Decisions

- Redirect handling and destination pinning are separate implementation cards.
- Redirects are limited to three and must remain on identical scheme, host, and
  effective port.
- HTTP connections are pinned to the recorded IP when one exists.
- Saved hostnames are metadata for HTTP `Host`, TLS SNI, and certificate
  verification; they are not alternate connection destinations.
- Ambient HTTP proxy settings must not affect target verification, browsing,
  probing, or extraction.
- `SMBSeekConfig` owns the persisted HTTP TLS-verification default.
- Scan-dialog TLS choices are transient per-run overrides.
- ZIP import never uses `extractall`.
- The 448 pass-only handlers are audited and remediated in the current wave,
  using risk-ordered batches of no more than 40 handlers.
- FTP CRLF is defense in depth, not a currently exploitable security finding.
- Python 3.8 end-of-life is recorded but not remediated in this wave.

## Sources

Primary project and standards inputs:

- [AI Agent Field Guide](https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_FIELD_GUIDE.md)
- [AI Agent Documentation Style Guide](https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_DOC_STYLE_GUIDE.md)
- [AI Agent Code Review Guide](https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_CODE_REVIEW_GUIDE.md)
- [OWASP SSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
- [Python urllib.request](https://docs.python.org/3/library/urllib.request.html)
- [Python zipfile](https://docs.python.org/3/library/zipfile.html)
- [Python ssl](https://docs.python.org/3/library/ssl.html)
- [Pillow Image reference](https://pillow.readthedocs.io/en/stable/reference/Image.html)
- [CWE-22 Path Traversal](https://cwe.mitre.org/data/definitions/22.html)
- [CPython FTP newline fix](https://github.com/python/cpython/issues/74305)
- [Python 3.8 release schedule and EOL](https://peps.python.org/pep-0569/)
