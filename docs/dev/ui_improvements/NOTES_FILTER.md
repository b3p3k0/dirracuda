# Server List: Notes column and Filters dropdown

The problem: hosts could have notes, but the Server List never showed which ones did.
You had to hover over each row or open its details to find out. The filter row was also out of room, with five separate
checkboxes.

## What changed

- New `Notes` column between `Extracted` and `Risk`: `✔` if the host has
  non-blank notes, otherwise `○`. It reads `row["notes"]`, which the list query
  already loaded from `host_user_flags` / `ftp_user_flags` / `http_user_flags`. No
  schema change.
- The five quick-filter checkboxes moved into one `Filters ▾` popover, plus a new
  `Has notes` option. You can tick any combination. The button shows the active count.
- When the details popup closes after a notes edit, the row's cell updates
  straight away (`notes_callback` → `ServerListWindow._on_notes_saved`).

HI decisions (2026-10-02): a stay-open popover, all five filters plus Has notes,
column after Extracted, desktop only. The Web UI Results page does not select
notes in its list query yet, so a Web UI Notes column is a separate follow-up card.

## Lessons

- A Tk menu checkbutton is the obvious multi-select dropdown, but the default menu
  bindings unpost the menu before each invoke, so picking N filters takes N
  opens ([Tk menu docs](https://www.tcl-lang.org/man/tcl8.6/TkCmd/menu.htm)).
  A borderless `Toplevel` with plain checkbuttons stays open.
- Bind the new widgets to the **existing** `BooleanVar`s. Prefs and templates
  store values by variable key, so swapping widgets needs no data migration. A new
  filter still has to be added in all five places in `actions/templates.py`
  (reset, load, persist, capture, apply).
- Close-on-outside-click binds `<Button-1>` on the window's toplevel with
  `add="+"` and unbinds that exact funcid. In Python < 3.13,
  `unbind(seq, funcid)` cleared every binding on the sequence. The venv is 3.14,
  and nothing else binds `<Button-1>` on the Server List toplevel. A test
  guards this.
- Popover checkbuttons belong to their own toplevel's bindtags. A click on them
  does not reach the outside-click handler on the main window.
- Put new columns after the positional `values[0]`/`values[1]` favorite/avoid
  toggle reads in `table.py`, or switch those reads to column names first.
- Under Xvfb there is no window manager, so `Esc` tests need `focus_force()`.
  Real desktop focus on an override-redirect window is an HI check.

## Validation

```
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_server_list_notes_filter.py gui/tests/test_server_list_card4.py gui/tests/test_sherlock_risk_column.py -q
xvfb-run -a ./venv/bin/python -m pytest gui/tests -q
```
