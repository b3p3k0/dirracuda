# Keymaster v1 ASCII Sketches

Date: 2026-04-25
Status: Draft baseline

## 1) Experimental Tab: `Keymaster`

```text
┌──────────────────────────────────────────────────────────────┐
│ Experimental Features                                        │
│  [SearXNG] [Reddit] [Dorkbook] [Keymaster]                  │
│                                                              │
│  Keymaster stores reusable API keys and lets you switch     │
│  the active key quickly for testing.                         │
│                                                              │
│  [ Open Keymaster ]                                          │
│                                                              │
│                                                   [ Close ]  │
└──────────────────────────────────────────────────────────────┘
```

## 2) Keymaster Main Window

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Keymaster                                                                  │
│ Manage reusable API keys for rapid testing key rotation.                  │
│                                                                            │
│ Search: [_______________________________________________]                 │
│                                                                            │
│ ┌────────────────────────────────────────────────────────────────────────┐ │
│ │ Label            | Key Preview      | Notes          | Last Used      │ │
│ │────────────────────────────────────────────────────────────────────────│ │
│ │ Primary Paid     | skAB********9a12 | baseline       | 2026-04-25...  │ │
│ │ Backup Trial     | trL0********73ff | low allotment  |                │ │
│ └────────────────────────────────────────────────────────────────────────┘ │
│                                                                            │
│ 2 key(s).                                                                  │
│                                                                            │
│ [ Add ] [ Apply ] [ Edit ] [ Delete ]                                      │
└────────────────────────────────────────────────────────────────────────────┘
```

## 3) Add/Edit Modal

```text
┌──────────────────────────────────────────────────────────────┐
│ Add API Key                                                  │
│                                                              │
│ Label:    [______________________________________________]  │
│ API Key:  [************************************]             │
│                                                              │
│ Notes:                                                       │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │                                                          │ │
│ └──────────────────────────────────────────────────────────┘ │
│                                                              │
│ Validation: Label and API Key are required                  │
│                                                              │
│                                  [ Cancel ] [ Save ]         │
└──────────────────────────────────────────────────────────────┘
```

## 4) Context Menu Parity

```text
Right-click selected row:
  Add
  Apply
  Edit
  Delete
```

## 5) Apply UX Equivalence

```text
Double-click row      ─┐
Context: Apply        ─┼─> _apply_selected_key()
Button: Apply         ─┘

_apply_selected_key():
  1) Validate selection
  2) Persist shodan.api_key to active config
  3) Mark row as last-used
  4) Show status/feedback
```

## 6) Delete Confirmation

```text
┌──────────────────────────────────────────────────────────────┐
│ Confirm Delete                                               │
│                                                              │
│ Delete selected key entry?                                   │
│ "Backup Trial"                                               │
│                                                              │
│                                  [ Cancel ] [ Delete ]       │
└──────────────────────────────────────────────────────────────┘
```
