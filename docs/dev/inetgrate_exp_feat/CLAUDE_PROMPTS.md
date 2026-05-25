# Integrate Experimental Features Into Main - CLAUDE PROMPTS

Use one card prompt at a time. HI sends prompt to Claude DA. RA reviews results.

## Universal Header

```text
You are Claude acting as DA for Dirracuda. Codex is RA and will review your work.

Repo: /home/kevin/DEV/dirracuda
Branch: development

Read before changing files:
- README.md
- CLAUDE.md
- docs/TECHNICAL_REFERENCE.md
- docs/dev/inetgrate_exp_feat/README.md
- docs/dev/inetgrate_exp_feat/SPEC.md
- docs/dev/inetgrate_exp_feat/ARCHITECTURE.md
- docs/dev/inetgrate_exp_feat/ROADMAP.md
- docs/dev/inetgrate_exp_feat/TASK_CARDS.md
- docs/dev/inetgrate_exp_feat/LESSONS_LEARNED.md
- docs/dev/inetgrate_exp_feat/RISK_REGISTER.md
- https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_FIELD_GUIDE.md
- https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_DOC_STYLE_GUIDE.md
- https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_DEVELOPMENT_GUIDE.md
- https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_CODE_REVIEW_GUIDE.md

Operating rules:
- Implement only the requested card.
- Censys is suspended and out of scope.
- Do not commit.
- Preserve existing behavior outside scope.
- Keep ./dirracuda as desktop runtime entrypoint.
- Keep gui/main.py shim-only.
- Check touched file line counts before and after.
- If any touched production code file exceeds 1700 lines, stop and propose modularization (tests/docs excluded from hard stop).
- Run targeted validation and report exact commands/results.

Response format:
- Issue:
- Root cause:
- Fix:
- Files changed:
- Validation run:
- Result:
- HI test needed? (yes/no + exact steps)
```

## C1 Prompt

```text
Use the Universal Header.

Implement C1 from docs/dev/inetgrate_exp_feat/TASK_CARDS.md.

Goal:
- Cut over dashboard Experimental naming/shell to Accessories with minimal UI risk.
- Keep Web UI, Dorkbook, Keymaster accessible.
- Do not break SearXNG/Reddit flows yet.
```

## C2 Prompt

```text
Use the Universal Header.

Implement C2 from docs/dev/inetgrate_exp_feat/TASK_CARDS.md.

Goal:
- Add provider selection scaffolding in Start Scan for Shodan/SearXNG/Reddit.
- Preserve existing SMB/FTP/HTTP behavior.
- No Censys exposure.
```

## C3 Prompt

```text
Use the Universal Header.

Implement C3 from docs/dev/inetgrate_exp_feat/TASK_CARDS.md.

Goal:
- Promote SearXNG launch path into core Start Scan flow using existing sidecar service/storage.
- Keep probe/promote behavior compatible with current sidecar promotion helpers.
- Update WebUI SearXNG behavior in this card to match promoted contract.
```

## C4 Prompt

```text
Use the Universal Header.

Implement C4 from docs/dev/inetgrate_exp_feat/TASK_CARDS.md.

Goal:
- Promote Reddit launch path into core Start Scan flow using existing sidecar service/storage.
- Preserve mode-specific validation and probe/promote behavior.
- Update WebUI Reddit behavior in this card to match promoted contract.
```

## C5 Prompt

```text
Use the Universal Header.

Implement C5 from docs/dev/inetgrate_exp_feat/TASK_CARDS.md.

Goal:
- Consolidate dashboard DB entrypoints into one Database surface.
- Preserve main DB and DB Tools behavior and add explicit legacy sidecar/import path.
- Implement one-time desktop migration prompt for existing sidecar data and persist migration state for WebUI status notice.
- Enforce defer policy: after `No defer`, suppress all future automatic startup prompts; migration becomes manual-only.
```

## C6 Prompt

```text
Use the Universal Header.

Implement C6 from docs/dev/inetgrate_exp_feat/TASK_CARDS.md.

Goal:
- Localize provider-specific config controls and reduce global provider clutter.
- Preserve backward compatibility for existing config reads.
- Remove legacy sidecar DB browsing controls from WebUI operator surfaces; keep migration notice/status only.
```

## C7 Prompt

```text
Use the Universal Header.

Implement C7 from docs/dev/inetgrate_exp_feat/TASK_CARDS.md.

Goal:
- Run regression/hardening checks for all touched surfaces and report exact outcomes.
- Classify failures as pre-existing vs introduced.
```

## C8 Prompt

```text
Use the Universal Header.

Implement C8 from docs/dev/inetgrate_exp_feat/TASK_CARDS.md.

Goal:
- Sync README.md and docs/TECHNICAL_REFERENCE.md with shipped behavior.
- Update lessons learned and risk register outcomes.
```

## RA Review Prompt (Findings-Only)

```text
You are Claude acting as DA receiving RA review findings.

Please address the findings below. Keep fixes scoped strictly to the cited issues.
Do not rewrite unrelated areas.

For each finding:
1) confirm whether you agree,
2) apply your preferred fix,
3) rerun targeted validation,
4) report result.

Findings:
<RA will paste findings list with file/line references>
```
