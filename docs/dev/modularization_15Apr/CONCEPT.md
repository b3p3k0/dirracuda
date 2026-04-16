Dirracuda Refactor & Re‑Organisation Plan (Development Branch)
Purpose

Dirracuda’s code base has grown rapidly as new protocols and features have been added. Several files have swelled well beyond the maintainability rubric the team has set (excellent: ≤1200 lines; good: ≤1500; acceptable: ≤1800; poor: 1801–2000; unacceptable: >2000). Oversized files increase cognitive load and hinder reuse, testing and onboarding. In the development branch the dashboard and unified browser implementations each exceed 3 000 lines and combine unrelated concerns, making them prime candidates for refactoring.

This document proposes a clear plan for breaking up large modules, deduplicating repeated code, and aligning the architecture with maintainability and extensibility goals. The plan is advisory only; no code changes are made by this document.

Current State & Rubric Evaluation
gui/components/dashboard.py – The dashboard module implements a “Mission Control” interface summarising metrics, progress and findings with drill‑down windows
teclado.com
. It contains UI construction, event handlers, scan management, log viewing, batch operations, config coercion and more. The file is over 3 300 lines long, far above the acceptable threshold. The docstring describes a vertical layout with header, body and status bar sections for situation awareness
teclado.com
, yet the implementation intermingles these responsibilities.
gui/components/unified_browser_window.py – This file (≈3 237 lines) defines UnifiedBrowserCore and three concrete subclasses (FtpBrowserWindow, HttpBrowserWindow and SmbBrowserWindow). It collects code that was previously duplicated across FTP and HTTP browser windows and implements adapter hooks for protocol‑specific functionality. However, consolidating all protocols into a single file creates a monolith where protocol‑specific logic, UI wiring and helper functions are tightly coupled.
Duplication of helpers – Functions such as _coerce_bool and _coerce_int appear in multiple modules (dashboard.py, ftp_scan_dialog.py, shared/tmpfs_quarantine.py). In shared/tmpfs_quarantine.py these helpers convert string values to booleans and clamp integers within a range, but identical logic is redefined elsewhere. Similarly, _format_file_size is defined in both file_viewer_window.py and unified_browser_window.py to convert byte counts to human‑readable strings. Duplicating such helpers increases maintenance risk and inconsistency.
Architecture guidance – The project’s technical reference describes the GUI entry point (gui/components/dashboard.py) and the component hierarchy. It emphasises that new protocols should be isolated and that GUI components should remain thin wrappers that call into back‑end workflows. Additionally, external articles on clean coding advocate splitting long code into smaller functions or files to improve maintainability, understandability, reusability, debuggability, extensibility and regression testing.
Refactoring Goals
Maintainability – Limit module size to ≤1 200 lines where possible; isolate concerns so that each file and class has a single purpose. Move duplicated logic into shared utilities.
Extensibility – Support adding new protocols or features without touching unrelated modules. Adhere to the adapter hooks pattern described in UnifiedBrowserCore so that new protocols only implement required behaviours.
User experience preservation – Ensure that splitting files does not change public APIs or break existing workflows. Preserve the dashboard layout and protocol browser behaviours, but improve internal structure.
Proposed File Decomposition
1. Dashboard Refactoring

Current issues: The dashboard module mixes UI layout, scan management, log viewer, configuration coercion and batch operations. It also defines its own _coerce_bool helper. This violates the single‑responsibility principle and makes testing and changes risky.

Recommended breakdown:

gui/dashboard/__init__.py – Turn the dashboard into a package. This file can expose a factory function or class to construct the full dashboard widget so external code continues to import DashboardWidget from one place.
gui/dashboard/layout.py – Contains pure UI layout definitions: creation of header, body, progress section and status bar. Each major section becomes a separate class (e.g., DashboardHeader, DashboardBody) responsible for building their portion of the Tkinter GUI. This isolates widget construction from business logic.
gui/dashboard/controllers.py – Encapsulates non‑UI behaviours such as scan management (start/stop, progress tracking), log handling, theme toggling and batch operations. It interacts with backend interfaces but does not touch widget creation. Methods currently embedded in DashboardWidget for starting scans, updating progress and handling bulk probe operations should move here.
gui/dashboard/utils.py – Houses helper functions duplicated across the project (e.g., _coerce_bool, _coerce_int, file‑size formatting if required by the dashboard). Import this module wherever coercion is needed rather than redefining functions.
gui/dashboard/widget.py – Provides the high‑level DashboardWidget class. It composes the layout classes and controllers, wiring event callbacks. The class remains the public interface but is now thin, delegating responsibilities to the modules above. Expose it via __init__.py.
gui/log_viewer package – If the log viewer in the dashboard is substantial, consider moving it into its own package (similar to file_viewer_window.py), with log_viewer.py implementing the widget and any associated controllers. The dashboard controller can then instantiate the log viewer as needed.

This decomposition ensures each module is under ~1 200 lines and focuses on a single aspect: UI definition, business logic, or utilities. It makes it easier to test, update or replace individual components without affecting the rest of the dashboard.

2. Unified Browser Refactoring

Current issues: unified_browser_window.py implements a base class plus three protocol implementations in one file. It also contains helper functions (_format_file_size, _coerce_bool) and lazy imports. This results in a dense file (>3 000 lines) where changes for one protocol can inadvertently affect others.

Recommended breakdown:

gui/browsers/__init__.py – New package to group browsing functionality. Export convenience functions like open_ftp_browser, open_http_browser and open_smb_browser to maintain the existing API.
gui/browsers/core.py – Contains the UnifiedBrowserCore class and general helpers. The core defines the abstract adapter hooks documented in the original file and implements shared UI logic, navigation, download and view behaviours. Helpers like _format_file_size and _coerce_bool should be imported from a shared utility module rather than defined here.
gui/browsers/ftp_browser.py, http_browser.py, smb_browser.py – Each subclass implements the protocol‑specific adapter hooks and protocol‑specific logic (e.g., path handling, configuration loading, concurrency tuning). Move lazy imports and configuration loaders into these modules. Each file should remain well under 1 200 lines.
gui/browsers/utils.py – Place common helper functions needed by browser modules, such as format_file_size (moved from file_viewer_window.py) and any concurrency wrappers. Optionally include decorators for running tasks in threads and updating the UI via the UIDispatcher described in the technical reference.
Protocol registration – Provide a plugin mechanism so new protocols can be registered without modifying the core. For example, define an entry‑point registry (browser_protocols = {"ftp": FtpBrowserWindow, "http": HttpBrowserWindow, …}) in browsers/__init__.py. This dictionary can be loaded dynamically or extended by user plugins.

By isolating each protocol into its own module, changes to SMB will not touch FTP or HTTP. Shared behaviours remain in core.py, aligning with the adapter hook design. The new package boundaries improve discoverability and prevent the single-file anti‑pattern.

Code Deduplication Recommendations
Consolidate helper functions – Move _coerce_bool, _coerce_int and similar parsers into a single utility module (e.g., gui/utils/coercion.py). The implementation from shared/tmpfs_quarantine.py converts strings like “1”, “yes” to booleans and clamps integers within ranges; reuse this across the entire codebase. Replace duplicate definitions in dashboard.py, ftp_scan_dialog.py and other files with imports from this utility.
Centralise file‑size formatting – Move _format_file_size into a common module (e.g., gui/utils/filesize.py) and import it in file_viewer_window.py, browser modules and anywhere else it is needed. Avoid reimplementing the same logic, which can lead to inconsistent units or rounding.
Shared concurrency wrappers – Browser windows and scanning controllers start threads and update progress in similar ways. Abstract thread launching, queue management and UI dispatch into reusable helpers or decorators (e.g., run_in_thread(fn, on_complete=None)). This simplifies code and reduces duplication of thread boilerplate.
Config loading – Several modules implement _load_*_browser_config or read INI files. Provide a single configuration loader in gui/utils/config.py that accepts a section name and returns typed values using the coercion helpers. This avoids copy‑pasting parsing logic.
Common viewer and log components – If the file viewer, image viewer and log viewer share behaviour (e.g., scrollable text display, syntax highlighting), extract a base viewer component in gui/viewers/base.py and derive FileViewerWindow, ImageViewerWindow and LogViewerWindow from it. Reusing widgets reduces duplication and improves consistency.
Overall Architectural Recommendations
Adopt a clear separation of concerns – Use patterns such as Model‑View‑Controller (MVC) or Model‑View‑ViewModel (MVVM) to separate UI rendering, business logic and data/state management. The technical reference already distinguishes between CLI workflows and GUI components; ensure the GUI remains a thin layer over back‑end services.
Embrace modular packaging – Organise the repository into top‑level packages such as gui, cli, backend, utils and plugins. Each package should have a clear purpose and expose well‑defined interfaces. Avoid monolithic files that combine unrelated functions.
Use asynchronous I/O where appropriate – Many operations (FTP/HTTP listing, downloading, scanning) are I/O bound. Consider using Python’s asyncio or concurrent.futures to manage concurrency rather than manual thread management. Provide an abstraction layer so that UI code can await results without blocking.
Establish testing and linting frameworks – Large files often grow because there are no tests enforcing modular design. Introduce unit tests for controllers, utilities and protocol implementations. Use static analysis tools (e.g., flake8, pylint) with maximum‑line‑length rules aligned with the rubric. Ensure contributions failing the line‑length rubric are flagged in CI.
Document public APIs and design decisions – As modules are split, update or create docstrings and developer documentation. Explain adapter hooks and expected behaviours for new protocols to guide future contributors. Document the reasoning behind the new package structure to avoid regression into monolithic patterns.
Plan incremental refactoring – Because the codebase is large and used by end‑users, refactor in small, well‑reviewed PRs. Start by introducing new utility modules and updating imports, then move specific responsibilities into new files. Maintain backwards‑compatible APIs until all usages are migrated.
Suggested Implementation Roadmap
Create utility modules – Add gui/utils/coercion.py, filesize.py, config.py and other small helpers. Move duplicated functions into these modules. Replace references in existing code. This step is low risk and reduces duplication quickly.
Introduce packages – Create the gui/dashboard and gui/browsers packages. Start by moving the existing monolithic files into these packages (e.g., rename dashboard.py to gui/dashboard/widget.py and unified_browser_window.py to gui/browsers/core.py). Add __init__.py files that import and expose the existing classes so imports remain stable. This allows team members to work in the new directory structure while the refactor proceeds.
Extract helper modules – Gradually extract sections of code from widget.py into layout.py, controllers.py and utils.py within gui/dashboard. Do the same for browser protocols: move subclass definitions into their own modules (ftp_browser.py, etc.) while leaving UnifiedBrowserCore in core.py.
Implement plugin registry – Add a registry in gui/browsers/__init__.py mapping protocol names to browser classes. Update caller code to use this registry instead of directly referencing classes. This sets the stage for third‑party protocols.
Update imports and tests – Refactor import statements across the codebase to reference the new modules. Introduce unit tests for extracted functions and controllers, ensuring behaviour remains unchanged. Keep integration tests around existing GUI flows to catch regressions.
Clean up and document – Once code is split and tests pass, remove unused code sections and update documentation. Add guidelines for future contributions: adhere to the file size rubric, place helpers in shared utilities, and follow the new architecture.
Conclusion

Splitting oversized modules and deduplicating helper functions will make Dirracuda easier to understand, maintain and extend. By turning monolithic files into cohesive packages, centralising utilities, and following patterns that isolate protocol‑specific code, the project will scale gracefully as new features and protocols are added. The steps outlined above provide a roadmap for the local team to implement these improvements incrementally while preserving the existing user experience. Following these recommendations will align the codebase with the principles of clean architecture and maintainability emphasised both in the project’s own technical reference and in industry guidance on splitting large codebases.