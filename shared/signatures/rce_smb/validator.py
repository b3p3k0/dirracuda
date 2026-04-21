"""
RCE Signature Validator

Python-side validation for RCE signature YAML files.
Validates required fields, data types, and logical consistency
without external JSON Schema dependencies.
"""

from typing import Dict, Any, List, Optional, Set
import logging

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Raised when signature validation fails."""
    pass


class SignatureValidator:
    """
    Validates RCE signature YAML structures.

    Performs comprehensive validation of signature files to ensure
    they contain required fields and logically consistent data.
    """

    REQUIRED_METADATA_FIELDS = {"cve_ids", "name", "severity", "description"}
    REQUIRED_HEURISTIC_FIELDS = {"required_signals", "base_weight", "risk_band", "conditions"}
    VALID_SEVERITIES = {"low", "medium", "high", "critical"}
    VALID_RISK_BANDS = {"low", "minimal", "medium", "moderate", "high"}

    def __init__(self):
        """Initialize validator."""
        self.validation_errors = []

    def validate_signature(self, signature_data: Dict[str, Any], filename: str = "unknown") -> bool:
        """
        Validate a complete signature structure.

        Args:
            signature_data: Parsed YAML signature data
            filename: Source filename for error reporting

        Returns:
            True if validation passes, False otherwise

        Raises:
            ValidationError: If validation fails with details
        """
        self.validation_errors = []
        self.filename = filename

        try:
            self._validate_structure(signature_data)
            self._validate_metadata(signature_data.get("metadata", {}))
            self._validate_heuristic(signature_data.get("heuristic", {}))
            self._validate_telemetry(signature_data.get("telemetry", {}))
            self._validate_references(signature_data.get("references", []))

            if self.validation_errors:
                error_msg = f"Validation failed for {filename}: " + "; ".join(self.validation_errors)
                raise ValidationError(error_msg)

            return True

        except Exception as e:
            if isinstance(e, ValidationError):
                raise
            raise ValidationError(f"Validation error in {filename}: {str(e)}")

    def _validate_structure(self, data: Dict[str, Any]) -> None:
        """Validate top-level signature structure."""
        if not isinstance(data, dict):
            self.validation_errors.append("Signature must be a dictionary")
            return

        required_sections = {"metadata", "heuristic"}
        for section in required_sections:
            if section not in data:
                self.validation_errors.append(f"Missing required section: {section}")

    def _validate_metadata(self, metadata: Dict[str, Any]) -> None:
        """Validate metadata section."""
        if not isinstance(metadata, dict):
            self.validation_errors.append("metadata must be a dictionary")
            return

        # Check required fields
        for field in self.REQUIRED_METADATA_FIELDS:
            if field not in metadata:
                self.validation_errors.append(f"Missing required metadata field: {field}")

        # Validate CVE IDs
        cve_ids = metadata.get("cve_ids")
        if cve_ids is not None:
            if not isinstance(cve_ids, list) or not cve_ids:
                self.validation_errors.append("cve_ids must be a non-empty list")
            else:
                for cve_id in cve_ids:
                    if not isinstance(cve_id, str) or not cve_id.startswith("CVE-"):
                        self.validation_errors.append(f"Invalid CVE ID format: {cve_id}")

        # Validate severity
        severity = metadata.get("severity")
        if severity and severity not in self.VALID_SEVERITIES:
            self.validation_errors.append(f"Invalid severity: {severity}. Must be one of {self.VALID_SEVERITIES}")

        # Validate name and description
        name = metadata.get("name")
        if name and (not isinstance(name, str) or not name.strip()):
            self.validation_errors.append("name must be a non-empty string")

        description = metadata.get("description")
        if description and (not isinstance(description, str) or not description.strip()):
            self.validation_errors.append("description must be a non-empty string")

    def _validate_heuristic(self, heuristic: Dict[str, Any]) -> None:
        """Validate heuristic section."""
        if not isinstance(heuristic, dict):
            self.validation_errors.append("heuristic must be a dictionary")
            return

        # Check required fields
        for field in self.REQUIRED_HEURISTIC_FIELDS:
            if field not in heuristic:
                self.validation_errors.append(f"Missing required heuristic field: {field}")

        # Validate required_signals
        required_signals = heuristic.get("required_signals")
        if required_signals is not None:
            if not isinstance(required_signals, int) or required_signals < 1:
                self.validation_errors.append("required_signals must be a positive integer")

        # Validate base_weight
        base_weight = heuristic.get("base_weight")
        if base_weight is not None:
            if not isinstance(base_weight, (int, float)) or not (0 <= base_weight <= 100):
                self.validation_errors.append("base_weight must be a number between 0 and 100")

        # Validate risk_band
        risk_band = heuristic.get("risk_band")
        if risk_band and risk_band not in self.VALID_RISK_BANDS:
            self.validation_errors.append(f"Invalid risk_band: {risk_band}. Must be one of {self.VALID_RISK_BANDS}")

        # Validate conditions
        conditions = heuristic.get("conditions")
        if conditions is not None:
            self._validate_conditions(conditions)

        # Validate boosters (optional)
        boosters = heuristic.get("boosters")
        if boosters is not None:
            self._validate_boosters(boosters)

    def _validate_conditions(self, conditions: List[Dict[str, Any]]) -> None:
        """Validate heuristic conditions."""
        if not isinstance(conditions, list) or not conditions:
            self.validation_errors.append("conditions must be a non-empty list")
            return

        for i, condition in enumerate(conditions):
            if not isinstance(condition, dict):
                self.validation_errors.append(f"condition {i} must be a dictionary")
                continue

            # Required fields for conditions
            required_fields = {"signal", "expectation", "rationale"}
            for field in required_fields:
                if field not in condition:
                    self.validation_errors.append(f"condition {i} missing required field: {field}")

            # Validate signal name
            signal = condition.get("signal")
            if signal and not isinstance(signal, str):
                self.validation_errors.append(f"condition {i} signal must be a string")

            # Validate rationale
            rationale = condition.get("rationale")
            if rationale and (not isinstance(rationale, str) or not rationale.strip()):
                self.validation_errors.append(f"condition {i} rationale must be a non-empty string")

    def _validate_boosters(self, boosters: List[Dict[str, Any]]) -> None:
        """Validate heuristic boosters."""
        if not isinstance(boosters, list):
            self.validation_errors.append("boosters must be a list")
            return

        for i, booster in enumerate(boosters):
            if not isinstance(booster, dict):
                self.validation_errors.append(f"booster {i} must be a dictionary")
                continue

            # Check required fields
            if "description" not in booster:
                self.validation_errors.append(f"booster {i} missing required field: description")
            if "weight" not in booster:
                self.validation_errors.append(f"booster {i} missing required field: weight")

            # Validate weight
            weight = booster.get("weight")
            if weight is not None:
                if not isinstance(weight, (int, float)) or weight < 0:
                    self.validation_errors.append(f"booster {i} weight must be a non-negative number")

    def _validate_telemetry(self, telemetry: Dict[str, Any]) -> None:
        """Validate telemetry section (optional)."""
        if not telemetry:  # Telemetry is optional
            return

        if not isinstance(telemetry, dict):
            self.validation_errors.append("telemetry must be a dictionary")
            return

        # Validate existing_sources
        existing_sources = telemetry.get("existing_sources")
        if existing_sources is not None:
            if not isinstance(existing_sources, list):
                self.validation_errors.append("existing_sources must be a list")
            else:
                for source in existing_sources:
                    if not isinstance(source, (str, dict)):
                        self.validation_errors.append("existing_sources items must be strings or dictionaries")

        # Validate needed_enhancements
        needed_enhancements = telemetry.get("needed_enhancements")
        if needed_enhancements is not None:
            if not isinstance(needed_enhancements, list):
                self.validation_errors.append("needed_enhancements must be a list")
            else:
                for enhancement in needed_enhancements:
                    if not isinstance(enhancement, (str, dict)):
                        self.validation_errors.append("needed_enhancements items must be strings or dictionaries")

    def _validate_references(self, references: List[Dict[str, Any]]) -> None:
        """Validate references section (optional)."""
        if not references:  # References are optional
            return

        if not isinstance(references, list):
            self.validation_errors.append("references must be a list")
            return

        for i, reference in enumerate(references):
            if not isinstance(reference, dict):
                self.validation_errors.append(f"reference {i} must be a dictionary")
                continue

            # Check required fields
            if "label" not in reference:
                self.validation_errors.append(f"reference {i} missing required field: label")
            if "url" not in reference:
                self.validation_errors.append(f"reference {i} missing required field: url")

            # Basic URL validation
            url = reference.get("url")
            if url and not isinstance(url, str):
                self.validation_errors.append(f"reference {i} url must be a string")
            elif url and not (url.startswith("http://") or url.startswith("https://")):
                self.validation_errors.append(f"reference {i} url must be a valid HTTP/HTTPS URL")

    def get_validation_errors(self) -> List[str]:
        """Get list of validation errors from last validation."""
        return self.validation_errors.copy()