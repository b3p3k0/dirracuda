"""C11B — coerce_searxng_tuning and snap callback tests.

TestCoerceSearxngTuning: pure-Python, no Tk required.
TestSnapCallback: uses tk.Tcl() for variable/trace behavior (no display).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.scan_provider_options import (
    coerce_searxng_tuning,
    _make_snap_callback,
)


# ---------------------------------------------------------------------------
# coerce_searxng_tuning — no Tk needed
# ---------------------------------------------------------------------------

class TestCoerceSearxngTuning:
    # step=1 (request timeout)
    def test_step1_in_range(self):
        assert coerce_searxng_tuning(15, default=15, lo=5, hi=60, step=1) == 15

    def test_step1_clamp_low(self):
        assert coerce_searxng_tuning(2, default=15, lo=5, hi=60, step=1) == 5

    def test_step1_clamp_high(self):
        assert coerce_searxng_tuning(99, default=15, lo=5, hi=60, step=1) == 60

    # step=5 (short retry)
    def test_step5_rounds_down(self):
        assert coerce_searxng_tuning(12, default=30, lo=5, hi=60, step=5) == 10

    def test_step5_rounds_up(self):
        assert coerce_searxng_tuning(13, default=30, lo=5, hi=60, step=5) == 15

    def test_step5_midpoint_half_up(self):
        # 12.5 from lo=5: (12.5-5)/5=1.5 → floor(2.0)=2 → 5+10=15 (half-up, not 10)
        assert coerce_searxng_tuning(12.5, default=30, lo=5, hi=60, step=5) == 15

    # step=30 (long retry)
    def test_step30_below_midpoint(self):
        assert coerce_searxng_tuning(74, default=180, lo=60, hi=300, step=30) == 60

    def test_step30_at_midpoint_half_up(self):
        # 75 from lo=60: (75-60)/30=0.5 → floor(1.0)=1 → 60+30=90 (not 60)
        assert coerce_searxng_tuning(75, default=180, lo=60, hi=300, step=30) == 90

    def test_step30_exact_multiple(self):
        assert coerce_searxng_tuning(90, default=180, lo=60, hi=300, step=30) == 90

    # Float strings
    def test_float_string_step5(self):
        assert coerce_searxng_tuning("32.7", default=30, lo=5, hi=60, step=5) == 35

    def test_float_string_step1(self):
        assert coerce_searxng_tuning("15.7", default=15, lo=5, hi=60, step=1) == 16

    # Error inputs
    def test_nonnumeric_returns_default(self):
        assert coerce_searxng_tuning("bad", default=30, lo=5, hi=60, step=5) == 30

    def test_none_returns_default(self):
        assert coerce_searxng_tuning(None, default=30, lo=5, hi=60, step=5) == 30

    def test_pos_inf_returns_default(self):
        assert coerce_searxng_tuning(float("inf"), default=15, lo=5, hi=60, step=1) == 15

    def test_neg_inf_returns_default(self):
        assert coerce_searxng_tuning(float("-inf"), default=15, lo=5, hi=60, step=1) == 15

    def test_nan_returns_default(self):
        assert coerce_searxng_tuning(float("nan"), default=15, lo=5, hi=60, step=1) == 15


# ---------------------------------------------------------------------------
# _make_snap_callback — uses tk.Tcl() (no display required)
# ---------------------------------------------------------------------------

class TestSnapCallback:
    @pytest.fixture(autouse=True)
    def _setup(self):
        import tkinter as tk
        try:
            self.root = tk.Tcl()
        except Exception as exc:
            pytest.skip(f"tk.Tcl() unavailable: {exc}")
        self.dbl_var = tk.DoubleVar(master=self.root, value=15.0)
        self.disp_var = tk.StringVar(master=self.root, value="15s")
        self.cb = _make_snap_callback(self.dbl_var, self.disp_var, lo=5, hi=60, step=5)
        self.dbl_var.trace_add("write", self.cb)

    def test_snap_fires_on_write(self):
        self.dbl_var.set(12.0)
        assert self.dbl_var.get() == 10.0
        assert self.disp_var.get() == "10s"

    def test_snap_half_up_midpoint(self):
        self.dbl_var.set(12.5)
        assert self.dbl_var.get() == 15.0
        assert self.disp_var.get() == "15s"

    def test_backing_var_normalized(self):
        # 10.4 rounds to 10 (nearest step-5 multiple from lo=5: (10.4-5)/5=1.08 → 1 → 10)
        self.dbl_var.set(10.4)
        assert abs(self.dbl_var.get() - 10.0) < 1e-9

    def test_backing_var_already_snapped_not_rewritten(self):
        self.dbl_var.set(10.0)  # already a step multiple
        assert abs(self.dbl_var.get() - 10.0) < 1e-9

    def test_display_var_updated(self):
        self.dbl_var.set(27.0)
        assert self.disp_var.get() == "25s"

    def test_snap_at_lo_boundary(self):
        self.dbl_var.set(3.0)
        assert self.dbl_var.get() == 5.0
        assert self.disp_var.get() == "5s"

    def test_snap_at_hi_boundary(self):
        self.dbl_var.set(65.0)
        assert self.dbl_var.get() == 60.0
        assert self.disp_var.get() == "60s"

    def test_snap_no_infinite_recursion(self):
        # Setting var triggers callback; callback sets var if needed;
        # second set must not loop. We count set() calls.
        import tkinter as tk
        set_count = [0]
        orig_set = self.dbl_var.set

        def counting_set(val):
            set_count[0] += 1
            orig_set(val)

        self.dbl_var.set = counting_set
        self.dbl_var.set(12.0)
        # One explicit set, at most one correction set inside snap = 2 total.
        assert set_count[0] <= 2

    def test_long_retry_step30_snap(self):
        import tkinter as tk
        dbl = tk.DoubleVar(master=self.root, value=180.0)
        disp = tk.StringVar(master=self.root, value="180s")
        cb = _make_snap_callback(dbl, disp, lo=60, hi=300, step=30)
        dbl.trace_add("write", cb)
        dbl.set(75.0)
        assert dbl.get() == 90.0
        assert disp.get() == "90s"
