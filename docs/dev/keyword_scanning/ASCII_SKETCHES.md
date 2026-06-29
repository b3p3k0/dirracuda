# Sherlock ASCII Sketches

## Accessories Tab

```text
Accessories -> Sherlock

+----------------------------------------------------------+
| Sherlock                                                 |
+----------------------------------------------------------+
| [x] Run Sherlock after successful probe snapshots         |
| Case: (o) Ignore case   ( ) Match case                   |
|                                                          |
| Severity colors                                          |
| HIGH  [#ff4d4d] [Choose]                                |
| MED   [#ffa31a] [Choose]                                |
| LOW   [#ffff80] [Choose]                                |
|                                                          |
| Patterns                                           | ^ |  |
| +--------------------------------------------------+---+  |
| | Sev  Category      Pattern              Enabled  |   |  |
| | HIGH Credentials   *password*           [x]      |   |  |
| | HIGH Secrets       *.pem                [x]      | # |  |
| | MED  Finance       *payroll*            [x]      | # |  |
| | LOW  Internal      *confidential*       [x]      |   |  |
| | ... analyst-added rows continue ...              |   |  |
| +--------------------------------------------------+---+  |
|                                                    | v |  |
| [Add] [Edit] [Disable] [Restore Built-ins] [Save]        |
+----------------------------------------------------------+
```

## Server List

```text
+--------------------------------------------------------------------------------+
| Risk    | Host          | Proto | Probe Status | Shares | Last Probe | Notes    |
+--------------------------------------------------------------------------------+
| HIGH 3  | 10.0.0.12     | SMB   | Complete     | 8      | Today      | ...      |
| MED 1   | ftp.example   | FTP   | Complete     | -      | Today      | ...      |
| LOW 1   | web.example   | HTTP  | Complete     | -      | Today      | ...      |
|         | 10.0.0.44     | SMB   | Complete     | 3      | Yesterday  | ...      |
|         | 10.0.0.55     | SMB   | Not probed   | -      | -          | ...      |
+--------------------------------------------------------------------------------+

Actions: [Probe Selected] [Scan Sherlock Selected] [Extract Selected] [...]
```

## Scan-Time Flow

```text
User starts scan
      |
      v
Discovery / verification
      |
      v
Optional probe snapshot
      |
      v
If Sherlock-after-probe is enabled
      |
      v
Sherlock evaluates snapshot paths only
      |
      v
Persist latest summary + capped hit details
      |
      v
Desktop / Web display findings only
```

## Standalone Sherlock Flow

```text
User selects hosts in Server List
      |
      v
Load latest probe snapshot per host
      |
      +--> No current snapshot: skip and count
      |
      v
Match enabled Sherlock patterns against snapshot paths
      |
      v
Persist latest summary + capped hit details
      |
      v
Refresh Risk column, row tint, detail pane, Web badges
```

## PA/RA Supervision Flow

```text
PA writes card
      |
      v
Claude returns card plan only
      |
      v
RA reviews plan
      |
      +--> gaps found: revise prompt / request updated plan
      |
      v
HI approves implementation prompt
      |
      v
Claude implements one card
      |
      v
RA reviews diff + tests + docs + file sizes
      |
      v
Report PASS/FAIL; after HI acceptance, RA commits before next card
```

## V2 Accessories Tab

```text
Accessories -> Sherlock

+----------------------------------------------------------+
| Sherlock                                                 |
+----------------------------------------------------------+
| [x] Ignore case        [x] Run after probe               |
|                                                          |
| Severity colors                                          |
| High  [#ff4d4d] [Choose]  Med [#ffa31a] [Choose]        |
| Low   [#ffff80] [Choose]                                |
|                                                          |
| User colors                                              |
| User1 [        ] [Choose]  User2 [        ] [Choose]    |
| User3 [        ] [Choose]                                |
|                                                          |
| Patterns                                                 |
| [Manage Patterns...]                                    |
|                                                          |
| [Save]  Saved.                                           |
+----------------------------------------------------------+
```

## V2 Pattern Manager

```text
Sherlock Patterns

+--------------------------------------------------------------------+
| Sherlock Patterns                                                  |
+--------------------------------------------------------------------+
| +--------------------------------------------------------------+ |^||
| | On | Severity | User Tag | Category | Label | Pattern | Type | | ||
| | Yes| HIGH     | User1    | PII      | SSN   | *ssn*   |Custom|#||
| | Yes| HIGH     |          | Secrets  | PEM   | *.pem   |Built-in|
| | No | MED      | User2    | Finance  | Tax   | *tax*   |Custom| ||
| | ... many analyst rows ...                                      |v||
| +--------------------------------------------------------------+---+
|                                                                    |
| [Add] [Edit] [Enable/Disable] [Delete] [Restore Built-ins] [Close] |
+--------------------------------------------------------------------+
```

```text
Pattern Add/Edit

+--------------------------------------+
| Add Pattern                          |
+--------------------------------------+
| Label:      [ Payroll archive      ] |
| Category:   [ Finance              ] |
| Pattern:    [ *payroll*            ] |
| Severity:   [ MED v ]                |
| Color tag:  [ User2 v ]              |
| [x] Enabled                          |
|                         [Cancel] [OK]|
+--------------------------------------+
```

## V2 Probe Batch Summary

```text
+--------------------------------------------------------------------------+
| Probe Batch Summary                                                      |
+--------------------------------------------------------------------------+
| IP Address   | Protocol | Action | Result  | Risk   | Notes             |
| 10.0.0.12    | SMB      | Probe  | Success | HIGH 4 | 3 share(s)        |
| ftp.example  | FTP      | Probe  | Success | LOW 1  | 12 directorie(s)  |
| 10.0.0.44    | SMB      | Probe  | Success |        | No accessible ... |
+--------------------------------------------------------------------------+
```

## V2 Settings Save Flow

```text
Edit colors/patterns
      |
      v
Validate severity colors (#RRGGBB required)
      |
      v
Validate user colors (empty OR #RRGGBB)
      |
      v
Pattern dialog staged edits remain in memory
      |
      v
Save all Sherlock settings to sherlock.json
```

## V2 Tint Decision Flow

```text
Fresh Sherlock finding?
      |
      +-- No --> blank Risk cell, no tint
      |
      v
Any hit has User tag with configured color?
      |
      +-- Yes --> use highest-severity tagged hit's User color
      |
      v
No usable User color
      |
      v
Use HIGH/MED/LOW severity color
      |
      v
Risk text remains severity-based: HIGH n / MED n / LOW n
```

## V2 Post-Probe Summary Flow

```text
Probe saves snapshot
      |
      v
Run after probe enabled?
      |
      +-- No --> existing summary unchanged
      |
      v
Sherlock hook runs and persists latest result
      |
      v
Probe result row receives Sherlock display data
      |
      v
Batch Summary opens
      |
      v
If any row has Risk:
  add Risk column and tint finding rows
Else:
  keep current summary layout
```

## Pattern Manager Improvements

```text
Sherlock Patterns

+--------------------------------------------------------------------------------+
| Search [ password______________ ] Category [All v] Severity [All v] [Clear]    |
| User Tag [All v] Enabled [All v]                                                |
+--------------------------------------------------------------------------------+
| On | Sev | User Tag | Category      | Label          | Pattern        | Type    |
| Yes| HIGH| User1    | Credentials   | Password files | *password*     | Built-in|
| No | MED |          | Finance       | Payroll        | *payroll*      | Custom  |
| ...                                                                            |
+--------------------------------------------------------------------------------+
| [Add] [Edit] [Copy] [Enable/Disable] [Delete] [Restore Built-ins] [Export]     |
| [Close]                                                                        |
+--------------------------------------------------------------------------------+
```

```text
Pattern Add/Edit

+------------------------------------------+
| Label:      [ Payroll archive          ] |
| Category:   [ Finance                v ] |
| Pattern:    [ *payroll*                ] |
| Severity:   [ MED v ]                    |
| Color tag:  [ User2 v ]                  |
| [x] Enabled                              |
|                              [Cancel] [OK]|
+------------------------------------------+
```

## Pattern Manager Flows

```text
Edit built-in
      |
      v
Open Add dialog prefilled from built-in
      |
      v
Save as new custom pattern
      |
      v
Original built-in remains code-defined
```

```text
Delete selected rows
      |
      +-- Built-in --> stage key in builtin_deleted
      |
      +-- Custom ---> remove staged custom row
      |
      v
Main Sherlock Save persists staged settings
```

```text
Filter changed
      |
      v
Recompute visible rows from staged pattern list
      |
      v
Refresh table and clear selection
      |
      v
Bulk actions can affect visible selected rows only
```

```text
Export
      |
      v
Native Save As dialog
      |
      +-- Cancel --> no write, staged data unchanged
      |
      v
Write JSON metadata + all staged pattern rows
```
