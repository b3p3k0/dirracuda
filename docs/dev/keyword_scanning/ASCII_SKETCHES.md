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
