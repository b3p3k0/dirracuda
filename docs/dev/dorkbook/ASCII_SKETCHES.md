# Dorkbook UI Sketches

## Unified library — review draft, 2026-09-27

HI direction: one view, provider headings with queries beneath them, and an
**Apply to Search** action that saves the query as the default across runs.
Example dorks below illustrate layout; the expanded catalog is not finalized
or validated for live yield. Existing v1 sketches remain below as history.

```text
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ Dorkbook                                                                                 │
│ Saved queries for Shodan and Self-hosted Search                                           │
│                                                                                          │
│ Find dork [__________________________]  Topic [All topics ▾]  [Clear]                      │
│                                                                                          │
│ Dork                       Protocol  Topic      Query preview                Default     │
│ ──────────────────────────────────────────────────────────────────────────────────────── │
│ ▼ Shodan                                                                                 │
│   Default SMB Dork           SMB       General    smb authentication: disabled    ✓       │
│   Default FTP Dork           FTP       General    port:21 "230 Login successful"  ✓       │
│   Default HTTP Dork          HTTP      General    http.title:"Index of /"         ✓       │
│   Books — EPUB               HTTP      Books      http.title:"Index of /" …               │
│   Music — FLAC               HTTP      Music      http.title:"Index of /" …               │
│                                                                                          │
│ ▼ Self-hosted Search · SearXNG / DeGoog                                                    │
│   Open directories           —         General    intitle:"index of"              ✓       │
│ > Books — EPUB               —         Books      intitle:"index of" "epub"               │
│   Movies — folders           —         Video      intitle:"index of" "movies"             │
│   Photos — folders           —         Photos     intitle:"index of" "photos"             │
│                                                                                          │
│ ── Selected dork ─────────────────────────────────────────────────────────────────────   │
│ Books — EPUB · Built-in                                                                  │
│ Applies to: Self-hosted Search                                                           │
│ Query: intitle:"index of" "epub"                                                         │
│ Notes: Looks for directory pages mentioning EPUB. Operator support varies by engine.     │
│                                                                                          │
│ [Copy Query]                                                [Apply to Search]            │
│ Saves this query as your default for future searches. Does not start a search.            │
│ ──────────────────────────────────────────────────────────────────────────────────────── │
│ [Add Dork]                                                                  [Close]      │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

Behavior proposed for review:

- One scrollable grouped table. Both providers start expanded; headings can
  collapse. Provider headings are not actionable dorks.
- Search matches names, queries, and notes across both providers. Topic filtering
  combines with text search. Keep headings visible with a no-matches indication.
- Provider entry points focus the appropriate group in this same library.
- Selecting a dork changes the preview only. Apply to Search explicitly saves;
  double-click must not silently change a saved default.
- The preview shows the complete selectable query and notes with wrapping or
  scrolling; the table may abbreviate long text.
- For a Shodan row the destination reads, for example, `Shodan · HTTP`.
- Default marks reflect the saved query, independently for each Shodan protocol
  and for Self-hosted Search. The four marks in the sketch illustrate possible
  user settings, not a proposal to automatically activate a new web-search default.
- Built-ins remain read-only and italicized. Custom selection adds Edit and
  Delete controls; Copy Query and Apply to Search work for either row kind.
- After a successful apply, show `Saved as the Self-hosted Search default.` and
  refresh the mark and any matching open scan field. Failure keeps the previous
  successful state and shows an error. An in-flight scan retains its own query.
- The proposed shared library replaces provider-specific dork libraries.
  Existing scan query inputs are still available; this is not a second scan UI.

Implementation fit: Tk's existing Treeview supports hierarchical rows with data
columns, so the desktop can retain its current widget family.
[Tk Treeview manual](https://web.tcl.tk/man/tcl8.6/TkCmd/ttk_treeview.htm)

## Historical v1 UI contract

Date: 2026-04-19  
Status: Frozen baseline for v1 implementation

## 1) Experimental Tab: `Dorkbook`

```text
┌──────────────────────────────────────────────────────────────┐
│ Experimental Features                                        │
│  [SearXNG] [Reddit] [Dorkbook]                              │
│                                                              │
│  Dorkbook stores reusable dorks in a sidecar DB.     │
│  Built-ins stay read-only; custom dorks are editable.     │
│                                                              │
│  [ Open Dorkbook ]                                           │
│                                                              │
│                                                   [ Close ]  │
└──────────────────────────────────────────────────────────────┘
```

## 2) Dorkbook Main Window

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Dorkbook                                                                   │
│ Dorkbook stores reusable dorks by protocol.                         │
│                                                                            │
│  Tabs:  [ SMB ] [ FTP ] [ HTTP ]                                           │
│                                                                            │
│  Search: [_______________________________]                                 │
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ Nickname            | Query                             | Notes      │   │
│  │──────────────────────────────────────────────────────────────────────│   │
│  │ *Default SMB Dork*  | smb authentication: disabled      | shipped... │   │
│  │ My Fast Filter      | smb has_screenshot:true           | optional   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                            │
│  2 dork(s).                                                              │
│                                                                            │
│  [ Add ] [ Copy ] [ Edit ] [ Delete ]                                     │
└────────────────────────────────────────────────────────────────────────────┘
```

Notes:
1. Built-ins render italic.
2. Built-ins hide Edit/Delete actions.
3. Search is current-tab only.

## 3) Add/Edit Modal

```text
┌──────────────────────────────────────────────────────────────┐
│ Add SMB Dork                                                 │
│                                                              │
│ Nickname: [______________________________________________]  │
│ Query:    [______________________________________________]  │
│                                                              │
│ Notes:                                                       │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │                                                          │ │
│ │                                                          │ │
│ └──────────────────────────────────────────────────────────┘ │
│                                                              │
│ Query is required.   (validation area only when invalid)    │
│                                                              │
│                                  [ Cancel ] [ Save ]         │
└──────────────────────────────────────────────────────────────┘
```

## 4) Built-in vs Custom Action State

```text
Custom selected row:
  Buttons: Add, Copy, Edit, Delete
  Context menu: Add, Copy, Edit, Delete

Built-in selected row:
  Buttons: Add, Copy
  Context menu: Add, Copy
  (Edit/Delete hidden)
```

## 5) Context Menu Parity

```text
Right-click on custom row:
  Add
  Copy
  Edit
  Delete

Right-click on built-in row:
  Add
  Copy
```

## 6) Delete Confirmation + Session Mute

```text
┌──────────────────────────────────────────────────────────────┐
│ Confirm Delete                                               │
│                                                              │
│ Delete the selected Dorkbook dork?                         │
│ Default SMB Dork                                             │
│                                                              │
│ [ ] Hide this message (until app restart)                    │
│                                                              │
│                                  [ Cancel ] [ Delete ]       │
└──────────────────────────────────────────────────────────────┘
```

## 7) Empty and No-Match States

```text
Empty tab:
  "No dorks yet. Use Add to create one."

No search matches:
  "0 dork(s) match search."
```
