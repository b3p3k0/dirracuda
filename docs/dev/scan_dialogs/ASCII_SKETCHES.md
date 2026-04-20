# Start Scan Query Editor Rewire — ASCII Sketches

Date: 2026-04-20

## 1) Start Scan Protocol Row (unchanged control point)

```text
+--------------------------------------------------------------+
| Protocols: [x] SMB  [x] FTP  [x] HTTP     [Edit Queries]    |
+--------------------------------------------------------------+
```

`Edit Queries` opens the modeless Discovery Dorks editor below.

## 2) Discovery Dorks Editor (new dialog)

```text
[Discovery Dorks]  (modeless, single-instance, non-blocking)
+------------------------------------------------------------------+
| Discovery Dorks                                                   |
| Edit base queries used by SMB/FTP/HTTP discovery scans.          |
|------------------------------------------------------------------|
| SMB Base Query:  [ smb authentication: disabled              ]   |
|                  [Reset] [Default]                         [✓/✗] |
|                                                                  |
| FTP Base Query:  [ port:21 "230 Login successful"           ]   |
|                  [Reset] [Default]                         [✓/✗] |
|                                                                  |
| HTTP Base Query: [ http.title:"Index of /"                  ]   |
|                  [Reset] [Default]                         [✓/✗] |
|------------------------------------------------------------------|
| [Open Dorkbook]                           [Cancel] [Save]        |
+------------------------------------------------------------------+
```

Notes:
1. Save writes only the three dork keys.
2. Cancel closes with no writes.
3. No `grab_set()`; Dorkbook and Start Scan stay interactive.

## 3) Dorkbook Main Window (use action entrypoints)

```text
[Dorkbook - HTTP tab]
+--------------------------------------------------------------------------------+
| Search: [ ....................... ]                                            |
|--------------------------------------------------------------------------------|
| Nickname              | Query                          | Notes                 |
|--------------------------------------------------------------------------------|
| [Builtin] Dir Listing | http.title:"Index of /"        | default               |
| My Query              | http.html:"directory listing"   | broad                |
|--------------------------------------------------------------------------------|
| [Add] [Copy] [Use in Discovery Dorks] [Edit] [Delete]                         |
+--------------------------------------------------------------------------------+
```

Right-click context (row selected):

```text
Add
Copy
Use in Discovery Dorks
Edit/Delete (custom rows only)
```

Double-click row behavior:

```text
double-click row -> same as "Use in Discovery Dorks"
```

## 4) Workflow A — Normal Save

```text
Start Scan -> Edit Queries -> Discovery Dorks opens
-> user edits one or more rows
-> Save
-> validate all 3 rows (non-blank)
-> write dork keys to config
-> close editor
```

## 5) Workflow B — Reopen While Already Open

```text
Edit Queries
-> existing Discovery Dorks instance is focused/raised
-> no duplicate editor window is created
```

## 6) Workflow C — Dorkbook Populate (manual-save)

```text
Dorkbook row -> Use in Discovery Dorks
-> Discovery Dorks editor opens/focuses
-> matching protocol field is populated (unsaved)
-> user clicks Save in editor to persist
```

## 7) Workflow D — Validation Failure

```text
Save with any blank row
-> inline row status shows invalid
-> blocking validation error shown
-> no config write occurs
-> editor remains open for correction
```
