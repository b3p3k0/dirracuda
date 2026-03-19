# HTTP MVP Handoff (Cards 1-6)

Date: YYYY-MM-DD  
Audience: future agents continuing HTTP MVP refinement

## 1. Module Concept

- HTTP module is a parallel path to SMB/FTP.
- Keep one-active-scan lock semantics.
- Reuse existing UX and safety conventions.

## 2. Card Delivery Snapshot

### Card 1
- Status:
- Key outputs:

### Card 2
- Status:
- Key outputs:

### Card 3
- Status:
- Key outputs:

### Card 4
- Status:
- Key outputs:

### Card 5
- Status:
- Key outputs:

### Card 6
- Status:
- Key outputs:

## 3. Commits of Note

- `<hash>` `<message>`

## 4. Current Validation Status

- Latest full suite:
  - `<n> passed`
  - `<n> failed` (expected/unrelated if applicable)
- Latest targeted HTTP suite:
  - `<n> passed`
  - `<n> failed`

## 5. Architecture Quick Map

## Scan path

`Dashboard` -> `scan_manager/interface` -> `httpseek` -> `shared/http_workflow.py` -> `commands/http/*` -> `shared/database.py`

## Browser/probe path

`Dashboard/Server List` -> HTTP browser UI -> HTTP navigator -> probe cache JSON

## 6. Known Limits (Intentional MVP Boundaries)

1. TBD

## 7. Known Risks / Follow-up Targets

1. TBD

## 8. Operational Notes for Future Agents

1. `docs/dev/` and `gui/tests/` may be ignored in this repo context; force-add when needed.
2. Keep SMB/FTP regression as hard gate on every HTTP card.
3. Preserve clear automated/manual completion semantics.

