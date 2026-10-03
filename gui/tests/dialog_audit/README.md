# Dialog layout audit

Run from the checkout using its virtual environment:

```bash
./venv/bin/python scripts/run_dialog_audit.py --output /tmp/dirracuda-dialog-audit/latest
./venv/bin/python scripts/run_dialog_audit.py --scenario scan-results-completed,scan-results-long-error --profile desktop --theme both --output /tmp/results-audit
./venv/bin/python scripts/run_dialog_audit.py --list
```

Prerequisites: Xvfb (`xvfb-run`), KWin X11 (`kwin_x11`), `dbus-run-session`,
`xprop`, Tk, and Pillow with XCB support. No service credentials are needed.
Each display/theme combination gets a private X server and window manager;
each scenario gets a new process, temporary application paths from `get_paths()`,
settings, and databases. Workers time out after 60 seconds. Network connections,
subprocess launches, browser launches, and unexpected messageboxes are blocked.
Worker threads are inert; fixtures supply results directly. Real Tk modal waits
are inspected and dismissed with Tk-thread callbacks. All fixture windows and
temporary data are disposed when their process exits.

The default matrix is 1920×1080 at 96 DPI, 1366×768 at 96 DPI, and 1920×1080
at 144 DPI, each in light and dark themes. Tk scaling is explicitly set as well
as X-server DPI. Component fixtures are tests; application launch checks must
use `./dirracuda`, never `gui/main.py`.

## Evidence and interpretation

`index.html` links full-desktop PNGs, with the untouched default geometry first,
then every notebook page and relevant scroll positions. `report.json` contains
the inventory, expected result coverage, errors, widget rectangles, requested
sizes, and findings. Individual `result.json` and `worker.log` files make failures
reproducible. The runner exits nonzero for missing prerequisites, unknown
scenarios, missing results, worker failures/timeouts, clipping, or an uncovered
construction site. Old results cannot satisfy a failed worker.

Expected controls are explicit in `scenarios.py` and `extra_scenarios.py`.
Every managed visible control is measured, including controls beyond those
expectations. Inactive notebook pages are inspected after selecting them.
Intentionally collapsed containers are allowed unless a control is explicitly
required by the scenario. Labels and buttons must fit their requested size and
ancestor boundaries. Window placement must fit the test screen. A two-pixel
tolerance accommodates Tk border rounding. Tree row height is compared with
the actual font line height, even for empty tables, to detect text clipped
inside otherwise correctly sized data widgets.

Vertical overflow is permitted only in canvas bodies declaring scrolling;
capture intervals must collectively expose the entire control. Text, list, and
tree widgets permit data scrolling: their viewport must be visible, but all data
rows need not fit simultaneously. The runner captures their end positions too.
Fixed actions remain outside scroll bodies. It never enlarges a fixture window
to make a measurement pass.

Geometry checks do not prove visual correctness. Review screenshots alongside
measurements, especially custom drawing, overlapping siblings, table columns,
and dynamically changing text. An ambiguous result requires visual review;
do not accept it merely because the geometry checks passed. Add a representative
state or an explicit expected control when an omission exposes a gap. These
Linux/Tk artifacts make no Windows or macOS compatibility claim.
Mapped text controls captured as completely black produce a nonzero
`review-unpainted-control` finding: this catches incomplete X11 repainting,
which must be reviewed and recaptured rather than accepted as clean geometry.

## Coverage and maintenance

`inventory.py` discovers the 74 current `Toplevel` construction sites from ASTs
and associates them with scenario builders. A full report lists every site and
its disposition; adding a site without a fixture fails coverage. Shared browser
construction is exercised separately through SMB, FTP, and HTTP subclasses.
The unified scan form exercises inherited SMB controls. Accessories, config,
database tools, and results notebooks have every tab visited. There are 90
scenarios, including successful/interrupted/failed/unknown scans, empty and
populated results, a long error, provider expansions, advanced server filters,
and the dismissed warning.

One legacy construction site is excluded: `ScanDialog._create_dialog`, the
unreachable standalone SMB form. The dashboard uses `UnifiedScanDialog`; the
legacy form references a removed budget-dialog method. Historical sidecar
browsers remain covered because the Legacy menu still exposes them.

Native OS/Tk file pickers and standard Tk messageboxes are separate exclusions.
Their calls are stubbed where a custom progress fixture needs to proceed.
The application's custom `askyescancel` window is covered. The static inventory
is a guard against new construction sites, not proof of all possible runtime
states: extend fixtures when adding tabs, expanding sections, or new result
shapes. Give each new case representative data, required control text, and a
builder; teardown and scrolling are shared by the worker.

Audit and layout regression tests:

```bash
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_dialog_audit.py gui/tests/test_scan_results_layout.py
```

These include deliberately clipped/unmapped controls, allowed scrolling,
incomplete scroll reachability, wrapping action rows, and the withdrawn-window
Scan Results regression. Existing focus/position smoke tests additionally need
an isolated X server with a window manager and `DIRRACUDA_FOCUS_TESTS=1`.
