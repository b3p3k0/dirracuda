# Accessories Menu Redesign

Dirracuda currently includes an “Experimental” button on the dashboard that exposes a handful of non‑core modules.  As search providers like SearXNG and Reddit are promoted to core features, the experimental section becomes a misnomer.  This document outlines how to replace it with an “Accessories” menu better suited to housing auxiliary tools.

## 1. Motivation

* **Clarity:** The term “experimental” suggests unstable or incomplete features.  Once the primary search modules have graduated to core status, the remaining items (Dorkbook, WebUI, Keymaster) are not experimental in the same sense but rather supplemental tools.
* **Organisation:** Consolidating auxiliary functions under a single menu keeps the dashboard uncluttered and helps users find lesser‑used tools without digging through unrelated features.

## 2. Proposed behaviour

1. **Rename button:** On the dashboard, replace the “Experimental” button with **Accessories**.
2. **Menu contents:** When clicked, the button opens a small dialog or dropdown listing the available accessories:
   * **Dorkbook** – A notebook or log viewer for storing and organising dork queries and associated results.
   * **WebUI** – A local web interface for viewing Dirracuda data in a browser (if implemented).
   * **Keymaster** – A credential manager for storing and editing API keys and secrets.
3. **Modular expansion:** The accessories list should be generated dynamically from a registry similar to the current experimental feature registry.  Each accessory will provide its own label and a factory function to build its UI.  This allows adding or removing accessories without modifying the dashboard code.
4. **Styling and placement:** Use the same uniform styling guidelines from `ui_normalization.md` for the accessories dialog.  The dialog should be smaller than the main start‑scan dialog and may include a descriptive subtitle (“Supplementary tools for advanced use”).

## 3. Implementation guidelines

* **Registry refactor:** Migrate `gui/components/experimental_features/registry.py` into a more general module such as `gui/components/accessories/registry.py`.  Update each accessory’s registration to reflect that it is no longer experimental and ensure the `build_tab` functions use the unified styling.
* **Dashboard changes:** In `gui/dashboard/widget.py`, replace the binding of the existing “experimental_button” with an “accessories_button” and update the callback to show the accessories dialog.  Remove references to search providers from this menu.
* **Future accessories:** Ensure the registry and UI support lazy loading so that heavy accessories (e.g., WebUI) are not loaded until invoked.  Provide a tool tip next to each item to describe its purpose.

## 4. Deprecation of experimental naming

* **Documentation:** Update user documentation and tooltips to remove references to “experimental” features when referring to SearXNG and Reddit.  These now reside in the main application.
* **Folder structure:** Keep the existing `experimental/` folder for backward compatibility, but ensure new code is placed in appropriate namespaced directories (`providers/`, `accessories/`).  The `experimental/` folder can be phased out in a future release.

Replacing the experimental button with a clear accessories menu will improve the organisation of Dirracuda’s dashboard and signal to users that promoted search providers are no longer experimental.  It also provides a logical home for other utility modules that complement, but do not directly compete with, the core scanning workflow.