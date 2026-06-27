# Sherlock V1 Spec

## Summary

Sherlock is an optional, display-only exposure triage layer. It evaluates
existing probe snapshot paths and highlights rows where filenames, directory
names, or share/path segments suggest higher remediation priority.

The planning agent must not implement this feature directly. Implementation is
split into supervised cards in `TASK_CARDS.md` and `CLAUDE_PROMPTS.md`.

## In Scope

- Match normalized snapshot paths from SMB, FTP, and HTTP probe snapshots.
- Use enabled built-in and custom patterns.
- Support plain substring matching and `*` / `?` wildcard matching.
- Support case-insensitive default and case-sensitive option.
- Store latest Sherlock summary per host/protocol, including matched snapshot id.
- Store capped hit details for details panes and Web UI read-only display.
- Add Accessories tab titled `Sherlock`.
- Add standalone Server List action to scan selected existing hosts.
- Optionally run Sherlock immediately after successful probe snapshot persistence.
- Add Risk column and row tint for findings only.
- Add read-only Sherlock badges/details to Web UI results.
- Update README, technical docs, and lessons learned during closeout.

## Out Of Scope

- File downloads.
- File content reads.
- Authentication changes.
- Network probing initiated by Sherlock.
- Regex or custom query language.
- Fuzzy matching in V1.
- Marking hosts compromised.
- Blocking extraction or changing remediation state.
- Web UI pattern editing.

## Matching Behavior

- Input records are pure Sherlock path-entry objects derived from existing
  probe snapshots. The matcher must not read the database, filesystem, network,
  or protocol clients directly.
- Path-entry adapters may normalize already-loaded snapshot rows or raw snapshot
  dictionaries, but storage reads belong to later cards.
- Matching checks both the full normalized path and individual path segments.
- Share or container names are part of the normalized match path when present.
- Original/display path casing is preserved for output; case folding is used
  only for comparison in ignore-case mode.
- Plain patterns match as substrings.
- Patterns containing `*` or `?` use shell-style wildcard semantics.
- Case-insensitive mode is default.
- Highest severity across matches controls displayed severity.
- Hit count counts matched snapshot path/pattern pairs.
- Detail views show severity, category, label/pattern, and path.

## Severity Defaults

| Severity | Default color | Display text |
| --- | --- | --- |
| high | `#ff4d4d` | `HIGH n` |
| med | `#ffa31a` | `MED n` |
| low | `#ffff80` | `LOW n` |

Invalid custom colors must be rejected before save. Saved values must be
validated as `#RRGGBB`.

## Built-In Pattern Groups

- Credentials and secrets.
- Private keys and certificates.
- PII.
- Finance, payroll, tax.
- HR, legal, customer data.
- Backups and database dumps.
- Internal or confidential labels.

Built-ins are enabled by default, disable-able, and restorable. Custom patterns
are editable.

## Persistence Contract

Sherlock stores latest-result state, not historical scans. The persisted result
must include enough data to decide whether the result is current for a host:

- host type.
- protocol server id.
- latest probe snapshot id used.
- highest severity.
- total hit count.
- scanned timestamp.
- capped hit detail rows.

If no current snapshot exists, Sherlock skips the host and records/report counts
honestly. The Server List Risk cell stays blank.

## UI Contract

- Accessories tab label is `Sherlock`.
- Pattern table is fixed-height with visible vertical scrollbar, mouse wheel,
  and keyboard arrow navigation.
- The Risk column is blank for clear, unscanned, stale, or no-snapshot rows.
- Findings show text, for example `HIGH 3`, plus row tint.
- Color is never the only signal.
- Start Scan runtime controls may mirror `Run after probe`; it edits the same
  Sherlock settings shard as Accessories and is not a one-scan-only option.
- Detail surfaces may explain no snapshot, skipped, or 0 hits, but the table
  stays quiet.

## Validation Contract

Each implementation card must report:

- Issue.
- Root cause.
- Fix.
- Files changed.
- Validation run.
- Result.
- HI test needed.

Each touched file's line count must be checked before and after. Any file over
1700 lines requires a pause and modularization proposal before continuing.
