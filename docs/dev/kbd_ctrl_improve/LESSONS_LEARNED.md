# Lessons Learned - Keyboard Control Enhancement

Date: 2026-06-30

1. Treat the old `docs/dev/add_keybindings/` folder as historical context, not
   authority. Runtime plus root quickref win when they agree.
2. Keyboard shortcuts need focus-aware tests. A shortcut that is fine on a tree
   can be hostile inside an Entry, Spinbox, Combobox, or Text widget.
3. Parent-window bindings rely on Tk's binding order. Tests should assert both
   dispatch and pass-through behavior, not only that bindings exist.
4. `dirracuda` at 1700 lines is a hard planning constraint. Touching it casually
   is how tiny changes become cleanup debt.
5. Keep shortcut hints short. If the hint needs a paragraph, the shortcut
   contract is probably too complicated.
6. The supplied root style-guide URL is stale; use the current
   `agent_sops/AI_AGENT_DOC_STYLE_GUIDE.md` path.
