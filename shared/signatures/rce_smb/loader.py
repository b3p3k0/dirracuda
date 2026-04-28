"""
RCE Signature Loader

YAML signature loading and parsing functionality.
Handles file discovery, YAML parsing, and validation orchestration.
"""

import yaml
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

from .validator import SignatureValidator, ValidationError
from shared.path_service import get_paths, get_legacy_paths, select_existing_path

logger = logging.getLogger(__name__)
_PATHS = get_paths()
_LEGACY = get_legacy_paths(paths=_PATHS)
DEFAULT_SIGNATURES_DIR = select_existing_path(
    _PATHS.signatures_rce_dir,
    [
        _LEGACY.flat_home_root / "conf" / "signatures" / "rce_smb",
        _LEGACY.repo_signatures_dir / "rce_smb",
    ],
)


@dataclass
class Signature:
    """Represents a loaded and validated RCE signature."""
    cve_ids: List[str]
    name: str
    severity: str
    description: str
    required_signals: int
    base_weight: float
    risk_band: str
    conditions: List[Dict[str, Any]]
    boosters: List[Dict[str, Any]]
    telemetry: Dict[str, Any]
    references: List[Dict[str, Any]]
    source_file: str
    verdict_mapping: Dict[str, List[Dict[str, Any]]]  # Maps verdict type to conditions

    @classmethod
    def from_yaml_data(cls, data: Dict[str, Any], source_file: str) -> "Signature":
        """Create Signature instance from validated YAML data."""
        metadata = data["metadata"]
        heuristic = data["heuristic"]

        # Parse verdict_mapping from heuristic section
        # Format: confirmed_when, likely_when, not_assessable_when, not_vulnerable_when
        verdict_mapping = heuristic.get("verdict_mapping", {})

        return cls(
            cve_ids=metadata["cve_ids"],
            name=metadata["name"],
            severity=metadata["severity"],
            description=metadata["description"],
            required_signals=heuristic["required_signals"],
            base_weight=float(heuristic["base_weight"]),
            risk_band=heuristic["risk_band"],
            conditions=heuristic["conditions"],
            boosters=heuristic.get("boosters", []),
            telemetry=data.get("telemetry", {}),
            references=data.get("references", []),
            source_file=source_file,
            verdict_mapping=verdict_mapping
        )


class SignatureLoadError(Exception):
    """Raised when signature loading fails."""
    pass


class SignatureLoader:
    """
    Loads and validates RCE signatures from YAML files.

    Provides centralized signature discovery, loading, and validation
    with comprehensive error handling and logging.
    """

    def __init__(self, signatures_dir: Optional[str] = None):
        """
        Initialize signature loader.

        Args:
            signatures_dir: Directory containing signature YAML files.
                          Defaults to conf/signatures/rce_smb in project root.
        """
        if signatures_dir is None:
            self.signatures_dir = DEFAULT_SIGNATURES_DIR
        else:
            self.signatures_dir = Path(signatures_dir)

        self.validator = SignatureValidator()
        self.loaded_signatures: List[Signature] = []
        self.load_errors: List[str] = []

    def discover_signature_files(self) -> List[Path]:
        """
        Discover YAML signature files in the signatures directory.

        Returns:
            List of Path objects for discovered YAML files

        Raises:
            SignatureLoadError: If signatures directory doesn't exist
        """
        if not self.signatures_dir.exists():
            raise SignatureLoadError(
                f"Signatures directory not found: {self.signatures_dir}. "
                "Expected RCE signature YAML files under conf/signatures/rce_smb."
            )

        if not self.signatures_dir.is_dir():
            raise SignatureLoadError(
                f"Signatures path is not a directory: {self.signatures_dir}. "
                "Expected RCE signature YAML files under conf/signatures/rce_smb."
            )

        # Find YAML files
        yaml_patterns = ["*.yaml", "*.yml"]
        signature_files = []

        for pattern in yaml_patterns:
            signature_files.extend(self.signatures_dir.glob(pattern))

        # Sort for consistent loading order
        signature_files.sort(key=lambda p: p.name)

        logger.info(f"Discovered {len(signature_files)} signature files in {self.signatures_dir}")
        return signature_files

    def load_signature_file(self, file_path: Path) -> Optional[Signature]:
        """
        Load and validate a single signature file.

        Args:
            file_path: Path to YAML signature file

        Returns:
            Signature instance if successful, None if failed
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                yaml_data = yaml.safe_load(f)

            if yaml_data is None:
                error_msg = f"Empty or invalid YAML file: {file_path.name}"
                logger.warning(error_msg)
                self.load_errors.append(error_msg)
                return None

            # Validate signature structure
            self.validator.validate_signature(yaml_data, file_path.name)

            # Create signature instance
            signature = Signature.from_yaml_data(yaml_data, file_path.name)
            logger.debug(f"Successfully loaded signature: {signature.name} from {file_path.name}")
            return signature

        except yaml.YAMLError as e:
            error_msg = f"YAML parsing error in {file_path.name}: {str(e)}"
            logger.error(error_msg)
            self.load_errors.append(error_msg)
            return None

        except ValidationError as e:
            error_msg = f"Validation error in {file_path.name}: {str(e)}"
            logger.error(error_msg)
            self.load_errors.append(error_msg)
            return None

        except Exception as e:
            error_msg = f"Unexpected error loading {file_path.name}: {str(e)}"
            logger.error(error_msg)
            self.load_errors.append(error_msg)
            return None

    def load_all_signatures(self) -> List[Signature]:
        """
        Load all signature files from the signatures directory.

        Returns:
            List of successfully loaded Signature instances

        Raises:
            SignatureLoadError: If signature discovery fails or no signatures loaded
        """
        self.loaded_signatures = []
        self.load_errors = []

        try:
            signature_files = self.discover_signature_files()
        except Exception as e:
            raise SignatureLoadError(f"Failed to discover signature files: {str(e)}")

        if not signature_files:
            raise SignatureLoadError(f"No signature files found in {self.signatures_dir}")

        # Load each signature file
        for file_path in signature_files:
            signature = self.load_signature_file(file_path)
            if signature:
                self.loaded_signatures.append(signature)

        # Report results
        total_files = len(signature_files)
        loaded_count = len(self.loaded_signatures)
        failed_count = len(self.load_errors)

        logger.info(f"Signature loading complete: {loaded_count}/{total_files} loaded successfully")

        if failed_count > 0:
            logger.warning(f"{failed_count} signature files failed to load")
            for error in self.load_errors:
                logger.warning(f"  - {error}")

        if loaded_count == 0:
            raise SignatureLoadError("No signatures loaded successfully")

        return self.loaded_signatures

    # Backward-compatible alias used by tests and older callers
    def load_all(self) -> List[Signature]:
        """Alias for load_all_signatures()."""
        return self.load_all_signatures()

    def get_signatures_by_severity(self, severity: str) -> List[Signature]:
        """Get signatures filtered by severity level."""
        return [sig for sig in self.loaded_signatures if sig.severity == severity]

    def get_signatures_by_cve(self, cve_id: str) -> List[Signature]:
        """Get signatures that cover a specific CVE."""
        return [sig for sig in self.loaded_signatures if cve_id in sig.cve_ids]

    def get_load_errors(self) -> List[str]:
        """Get list of errors from last load operation."""
        return self.load_errors.copy()

    def get_signature_count(self) -> int:
        """Get count of successfully loaded signatures."""
        return len(self.loaded_signatures)

    def get_signature_summary(self) -> Dict[str, Any]:
        """Get summary statistics about loaded signatures."""
        if not self.loaded_signatures:
            return {"total": 0, "by_severity": {}, "by_risk_band": {}}

        severity_counts = {}
        risk_band_counts = {}

        for sig in self.loaded_signatures:
            severity_counts[sig.severity] = severity_counts.get(sig.severity, 0) + 1
            risk_band_counts[sig.risk_band] = risk_band_counts.get(sig.risk_band, 0) + 1

        return {
            "total": len(self.loaded_signatures),
            "by_severity": severity_counts,
            "by_risk_band": risk_band_counts,
            "load_errors": len(self.load_errors)
        }
