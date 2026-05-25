# UI Normalisation and Styling Guidelines

Dirracuda’s interface currently exhibits uneven styling across different dialogs.  In particular, the Shodan quick scan dialog uses a custom, dense layout with extra padding and fonts that diverge from other parts of the application.  This document proposes guidelines to normalise the look and feel across all dialogs and outlines specific changes to bring the Shodan scan interface in line with these standards.

## 1. General styling principles

1. **Consistency first:** Use the same font family, size, and weight across all dialogs.  A sans‑serif font (e.g., “Segoe UI” or “DejaVu Sans”) at 10–12 pt works well for readability.  Avoid mixing font families.
2. **Minimal padding:** Each control should have modest horizontal and vertical padding (e.g., 4–8 px).  Excess padding wastes screen real estate and makes dialogs feel cluttered.
3. **Clear alignment:** Align labels and input widgets on a grid.  Use vertical spacing to separate groups of related fields and align actions (buttons) at the bottom right or centred, depending on context.
4. **Section headings:** When a dialog contains multiple logical groups (e.g., provider lists and options panels), add subtle section headers or separators to aid scanning.  Use bold text or small horizontal rules rather than large colourful banners.
5. **Colour palette:** Follow Dirracuda’s existing colour theme for backgrounds, foregrounds and accent colours.  If the application supports dark/light themes, ensure dialogs respond accordingly (e.g., using `get_theme()` from `gui.utils.style`).
6. **Control types:** Use native Tkinter or ttk widgets whenever possible to maintain a consistent appearance across platforms.  Avoid custom widgets unless they provide significant functional benefit.

## 2. Normalising the Shodan quick scan dialog

The current Shodan dialog includes nested frames, tinted backgrounds, unusual fonts, and non‑standard spacing.  To normalise it:

* **Flatten the layout:** Remove unnecessary container frames and use a single scrollable frame for the options area.  Place controls in a simple vertical or grid layout similar to the existing unified scan dialog.
* **Standardise the fonts:** Use the same font family and size as other dialogs.  Remove bold and italic text unless emphasising section headings.
* **Adjust padding:** Reduce margins around group boxes and buttons.  Ensure that the spacing between fields is consistent with other dialogs (e.g., 6 px vertical spacing).
* **Simplify labels:** Shorten label text where possible; use tool tips or inline hints for explanatory text rather than long sentences.  This makes the dialog less intimidating and easier to scan.
* **Remove decorative elements:** Elements like thick borders, drop shadows or tinted backgrounds should be removed.  Instead, rely on clean separators and whitespace to delineate sections.
* **Integrate provider‑specific options:** Move provider options (max results, concurrency, region filters, etc.) into the provider’s collapsible panel within the new unified start‑scan dialog (see start_scan_dialog.md).  The Shodan dialog will then become a panel rather than a standalone modal.

## 3. Dialog templates for consistency

To enforce uniform styling, introduce reusable dialog templates:

* **BaseDialog:** A base class that sets the common font, padding and colour scheme.  It can also implement standard button placement and keyboard shortcuts (Enter to accept, Escape to cancel).  All new dialogs should subclass `BaseDialog`.
* **SectionFrame:** A reusable frame that takes a title and a list of form fields, automatically applying spacing and optional collapsibility.  Use this for provider option panels.
* **FormField:** Helper functions to create labelled entries, comboboxes or checkboxes with consistent spacing and error highlighting.  This eliminates copy‑paste of styling logic.

## 4. Accessibility considerations

* **Keyboard navigation:** Ensure that all fields are reachable via Tab order and that checkboxes can be toggled with Space.  Provide mnemonic shortcuts for common actions (e.g., Alt+S for Start).
* **Contrast ratios:** Use colours with sufficient contrast against the background to meet accessibility guidelines.  This is especially important in dark mode.
* **Dynamic sizing:** Allow dialogs to resize gracefully.  Use `pack()` or `grid()` with weight settings to ensure fields grow appropriately when the window is resized.

## 5. Testing and iterative improvement

* **Visual review:** After implementing the new styles, perform a side‑by‑side comparison with existing dialogs to confirm cohesion.  Solicit feedback from test users to ensure the UI feels intuitive and professional.
* **Refactoring:** Use the normalization effort as an opportunity to refactor repeated UI code into reusable components.  This will reduce maintenance overhead and make it easier to apply global style changes in the future.

Adhering to these guidelines will result in a cohesive and professional interface across all of Dirracuda’s dialogs, reducing user confusion and improving the perceived quality of the application.