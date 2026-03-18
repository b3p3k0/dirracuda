# FTP DB Import Workstream

Status: Planning in progress (started 2026-03-17).

This folder is the isolated planning workspace for the FTP+SMB database import/integration workstream. It intentionally avoids mixing with earlier FTP MVP planning artifacts.

## Objective

Deliver a dual-row protocol model in the host browser:

```text
S 1.2.3.4  SMB shares...
F 1.2.3.4  FTP directories...
```

Key behavior:
- Same IP can appear twice (SMB row + FTP row).
- User flags and probe/extracted states are protocol-specific.
- Deleting one row deletes only that protocol row.

## Document Map

- `01-LOCKED_DECISIONS.md` - Product decisions explicitly approved by HI.
- `02-ARCHITECTURE_SKETCH.md` - ASCII architecture and migration/query flow.
- `03-CLAUDE_TASK_CARDS.md` - Claude-ready execution cards and prompts.
- `04-QA_QC_GATES.md` - Validation checklist and acceptance gates.
- `05-RISKS_EDGE_CASES.md` - Risks, assumptions, and failure modes.
