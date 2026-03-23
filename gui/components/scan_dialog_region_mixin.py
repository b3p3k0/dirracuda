"""
Country/region selection mixin for ScanDialog.

Extracted from scan_dialog.py (Slice 7B refactor).
All methods reference ``self.*`` attributes defined on ScanDialog and resolve
correctly via Python's MRO when this mixin is listed as a base class.

Do NOT import from scan_dialog.py — that would create a circular import.
``REGIONS`` is accessed as ``self.REGIONS`` (class attribute on ScanDialog).
``_MAX_COUNTRIES`` and ``parse_and_validate_countries`` come from the shared
validators helper module.
"""

from __future__ import annotations

from gui.components.unified_scan_validators import _MAX_COUNTRIES, parse_and_validate_countries


class _ScanDialogRegionMixin:
    """Mixin — country/region selection methods only; no ``__init__``."""

    # ------------------------------------------------------------------
    # Region UI state
    # ------------------------------------------------------------------

    def _update_region_status(self) -> None:
        """Update the status label showing selected regions and country count."""
        selected_regions = []
        total_countries = 0

        region_vars = [
            ("Africa", self.africa_var),
            ("Asia", self.asia_var),
            ("Europe", self.europe_var),
            ("North America", self.north_america_var),
            ("Oceania", self.oceania_var),
            ("South America", self.south_america_var)
        ]

        for region_name, region_var in region_vars:
            if region_var.get():
                selected_regions.append(region_name)
                total_countries += len(self.REGIONS[region_name])

        if selected_regions:
            if len(selected_regions) == 1:
                status_text = f"{selected_regions[0]} ({total_countries} countries)"
            else:
                status_text = f"{len(selected_regions)} regions ({total_countries} countries)"
        else:
            status_text = ""

        self.region_status_label.configure(text=status_text)

    def _select_all_regions(self) -> None:
        """Select all regional checkboxes."""
        self.africa_var.set(True)
        self.asia_var.set(True)
        self.europe_var.set(True)
        self.north_america_var.set(True)
        self.oceania_var.set(True)
        self.south_america_var.set(True)
        self._update_region_status()

    def _clear_all_regions(self) -> None:
        """Clear all regional checkboxes."""
        self.africa_var.set(False)
        self.asia_var.set(False)
        self.europe_var.set(False)
        self.north_america_var.set(False)
        self.oceania_var.set(False)
        self.south_america_var.set(False)
        self._update_region_status()

    # ------------------------------------------------------------------
    # Country resolution
    # ------------------------------------------------------------------

    def _get_selected_region_countries(self) -> list[str]:
        """Get all country codes from selected regions."""
        region_countries = []

        region_vars = [
            ("Africa", self.africa_var),
            ("Asia", self.asia_var),
            ("Europe", self.europe_var),
            ("North America", self.north_america_var),
            ("Oceania", self.oceania_var),
            ("South America", self.south_america_var)
        ]

        for region_name, region_var in region_vars:
            if region_var.get():
                region_countries.extend(self.REGIONS[region_name])

        return region_countries

    def _get_all_selected_countries(self, manual_input: str) -> tuple[list[str], str]:
        """Get combined list of manually entered and region-selected countries.

        Args:
            manual_input: Raw manual country input string

        Returns:
            Tuple of (combined_countries_list, error_message)
            If error_message is empty, validation succeeded
        """
        # Parse manual countries — via self for dynamic-dispatch compatibility
        manual_countries, error_msg = self._parse_and_validate_countries(manual_input)
        if error_msg:
            return [], error_msg

        # Get region countries
        region_countries = self._get_selected_region_countries()

        # Combine and de-duplicate
        all_countries = list(set(manual_countries + region_countries))
        all_countries.sort()

        if len(all_countries) > _MAX_COUNTRIES:
            return [], (
                f"Too many countries selected ({len(all_countries)}). "
                f"Maximum allowed: {_MAX_COUNTRIES}. Please reduce your selection."
            )

        return all_countries, ""

    def _parse_and_validate_countries(self, country_input: str) -> tuple[list[str], str]:
        """Parse and validate comma-separated country codes."""
        return parse_and_validate_countries(country_input)
