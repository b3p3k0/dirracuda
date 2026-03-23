"""
Country/region selection mixin for UnifiedScanDialog.

Extracted from unified_scan_dialog.py (Slice 4A refactor).
All methods reference ``self.*`` attributes defined on UnifiedScanDialog and
resolve correctly via Python's MRO when this mixin is listed as a base class.

Do NOT import from unified_scan_dialog.py — that would create a circular import.
``REGIONS`` is imported directly from ScanDialog; ``_MAX_COUNTRIES`` from the
validators helper module.
"""

from __future__ import annotations

from gui.components.scan_dialog import ScanDialog
from gui.components.unified_scan_validators import _MAX_COUNTRIES

REGIONS = ScanDialog.REGIONS


class _UnifiedScanDialogRegionMixin:
    """Mixin — country/region selection methods only; no ``__init__``."""

    def _get_selected_region_countries(self) -> list[str]:
        region_vars = [
            ("Africa", self.africa_var),
            ("Asia", self.asia_var),
            ("Europe", self.europe_var),
            ("North America", self.north_america_var),
            ("Oceania", self.oceania_var),
            ("South America", self.south_america_var),
        ]
        out = []
        for name, var in region_vars:
            if var.get():
                out.extend(REGIONS[name])
        return out

    def _get_all_selected_countries(self, manual_input: str) -> tuple[list[str], str]:
        manual, err = self._parse_and_validate_countries(manual_input)
        if err:
            return [], err

        region = self._get_selected_region_countries()
        all_countries = sorted(set(manual + region))

        if len(all_countries) > _MAX_COUNTRIES:
            return [], (
                f"Too many countries selected ({len(all_countries)}). "
                f"Maximum allowed: {_MAX_COUNTRIES}. Please reduce your selection."
            )
        return all_countries, ""

    def _update_region_status(self) -> None:
        if not self.region_status_label:
            return

        region_vars = [
            ("Africa", self.africa_var),
            ("Asia", self.asia_var),
            ("Europe", self.europe_var),
            ("North America", self.north_america_var),
            ("Oceania", self.oceania_var),
            ("South America", self.south_america_var),
        ]
        selected, total = [], 0
        for name, var in region_vars:
            if var.get():
                selected.append(name)
                total += len(REGIONS[name])

        if selected:
            text = f"{selected[0]} ({total} countries)" if len(selected) == 1 else f"{len(selected)} regions ({total} countries)"
        else:
            text = ""
        self.region_status_label.configure(text=text)

    def _select_all_regions(self) -> None:
        for var in (
            self.africa_var,
            self.asia_var,
            self.europe_var,
            self.north_america_var,
            self.oceania_var,
            self.south_america_var,
        ):
            var.set(True)
        self._update_region_status()

    def _clear_all_regions(self) -> None:
        for var in (
            self.africa_var,
            self.asia_var,
            self.europe_var,
            self.north_america_var,
            self.oceania_var,
            self.south_america_var,
        ):
            var.set(False)
        self._update_region_status()
