# Dorkbook v1 ASCII Sketches (UI Contract)

Date: 2026-04-19  
Status: Frozen baseline for v1 implementation

## 1) Experimental Tab: `Dorkbook`

```text
┌──────────────────────────────────────────────────────────────┐
│ Experimental Features                                        │
│  [SearXNG] [Reddit] [Dorkbook]                              │
│                                                              │
│  Dorkbook stores reusable dork recipes in a sidecar DB.     │
│  Built-ins stay read-only; custom recipes are editable.     │
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
│ Dorkbook stores reusable dork recipes by protocol.                         │
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
│  2 recipe(s).                                                              │
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
│ Delete the selected Dorkbook recipe?                         │
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
  "No recipes yet. Use Add to create one."

No search matches:
  "0 recipe(s) match search."
```

