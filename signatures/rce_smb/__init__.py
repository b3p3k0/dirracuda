"""
RCE SMB Signature Package

Signature-based RCE vulnerability detection for SMB enumeration.
Provides YAML-based signature loading, validation, and rule processing
for defensive security analysis.

This package implements manual signature management following defensive
security principles - no automatic signature downloads or updates.
"""

from .loader import SignatureLoader
from .validator import SignatureValidator
from .rules import RuleEngine

__version__ = "1.0.0"
__all__ = ["SignatureLoader", "SignatureValidator", "RuleEngine"]