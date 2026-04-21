"""
RCE Rules Engine

Processing engine for evaluating RCE signatures against host context.
Implements scoring logic and evidence collection for vulnerability assessment.
"""

import logging
import re
import sys
import os
from typing import Dict, List, Any, Optional, Set, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from .loader import Signature

# Import Verdict from the shared rce_scanner module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from shared.rce_scanner.verdicts import Verdict, highest_severity_verdict

logger = logging.getLogger(__name__)


@dataclass
class RuleMatch:
    """Represents a matched RCE signature with evidence and verdict."""
    signature: Signature
    score: float
    matched_conditions: List[Dict[str, Any]]
    applied_boosters: List[Dict[str, Any]]
    evidence: List[str]
    confidence: str
    verdict: Verdict = field(default=Verdict.INSUFFICIENT_DATA)
    not_assessable_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "signature_name": self.signature.name,
            "cve_ids": self.signature.cve_ids,
            "score": self.score,
            "confidence": self.confidence,
            "verdict": self.verdict.value,
            "matched_conditions": self.matched_conditions,
            "applied_boosters": self.applied_boosters,
            "evidence": self.evidence,
            "severity": self.signature.severity,
            "risk_band": self.signature.risk_band
        }
        if self.not_assessable_reason:
            result["not_assessable_reason"] = self.not_assessable_reason
        return result


@dataclass
class RuleEngineResult:
    """Complete results from rule engine evaluation."""
    total_score: float
    confidence_level: str
    matched_rules: List[RuleMatch]
    evidence: List[str]
    status: str
    timestamp: str
    overall_verdict: Verdict = field(default=Verdict.INSUFFICIENT_DATA)
    not_assessable_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "score": min(100, max(0, int(self.total_score))),  # Ensure 0-100 range
            "level": self.confidence_level,
            "verdict": self.overall_verdict.value,
            "matched_rules": [rule.to_dict() for rule in self.matched_rules],
            "evidence": self.evidence,
            "status": self.status,
            "timestamp": self.timestamp
        }
        if self.not_assessable_reasons:
            result["not_assessable_reasons"] = self.not_assessable_reasons
        return result


class RuleEngine:
    """
    RCE signature evaluation engine.

    Processes loaded signatures against host context to identify
    potential RCE vulnerabilities with confidence scoring.
    """

    # Signal extractors for different data sources
    # Aligned with FactCollector output names
    SIGNAL_EXTRACTORS = {
        # New aligned extractors (from SafeProbeRunner / FactCollector)
        "smb_dialect": lambda ctx: ctx.get("smb_dialect"),
        "signing_required": lambda ctx: ctx.get("signing_required", False),
        "compression_algos": lambda ctx: ctx.get("compression_algos", []),
        "has_compression": lambda ctx: len(ctx.get("compression_algos", [])) > 0,
        "smb1_possible": lambda ctx: ctx.get("smb1_possible", False),
        "ms17_010_status": lambda ctx: ctx.get("ms17_010_status"),
        "domain_role_hints": lambda ctx: ctx.get("has_domain_role_indicators", False),
        "ksmbd_marker": lambda ctx: ctx.get("ksmbd_marker", False),
        "samba_marker": lambda ctx: ctx.get("samba_marker", False),
        "shodan_product": lambda ctx: ctx.get("shodan_product", ""),
        "shodan_version": lambda ctx: ctx.get("shodan_version", ""),
        "shodan_os": lambda ctx: ctx.get("shodan_os", ""),
        "anonymous_access": lambda ctx: ctx.get("anonymous_access", False),

        # Legacy extractors for backward compatibility
        "smb.negotiation.supports_smb1": lambda ctx: ctx.get("smb1_possible", False) or any(
            "SMB1" in str(d) for d in ctx.get("smb_dialects", [])
        ),
        "smb.negotiation.supports_smb2": lambda ctx: any(
            str(d).startswith("SMB2") for d in ctx.get("smb_dialects", [])
        ),
        "smb.negotiation.supports_smb3": lambda ctx: any(
            str(d).startswith("SMB3") for d in ctx.get("smb_dialects", [])
        ),
        "host.os_detection": lambda ctx: ctx.get("detected_os", ctx.get("os_hints", {}).get("detected_os", "")).lower(),
        "host.shares_accessible": lambda ctx: len(ctx.get("accessible_shares", [])) > 0,
        "host.admin_shares_accessible": lambda ctx: any(
            share.lower() in ["admin$", "c$", "ipc$"] for share in ctx.get("accessible_shares", [])
        ),
        "host.anonymous_access": lambda ctx: ctx.get("anonymous_access", False) or ctx.get("auth_method", "") in ["anonymous", "guest", ""],
        "host.shodan_ports": lambda ctx: ctx.get("shodan_data", {}).get("ports", []),
        "host.shodan_vulns": lambda ctx: ctx.get("shodan_data", {}).get("vulns", []),
    }

    def __init__(self, signatures: List[Signature]):
        """
        Initialize rule engine with loaded signatures.

        Args:
            signatures: List of validated Signature instances
        """
        self.signatures = signatures
        self.evaluation_stats = {
            "total_evaluations": 0,
            "signatures_matched": 0,
            "conditions_evaluated": 0
        }

    def evaluate_host(self, host_context: Dict[str, Any]) -> RuleEngineResult:
        """
        Evaluate all signatures against a host context.

        Args:
            host_context: Dictionary containing host information from probe/access

        Returns:
            RuleEngineResult with scoring and evidence
        """
        self.evaluation_stats["total_evaluations"] += 1

        matched_rules = []
        all_evidence = []
        total_score = 0.0

        # Determine status based on available data
        status = self._determine_status(host_context)

        for signature in self.signatures:
            try:
                rule_match = self._evaluate_signature(signature, host_context)
                if rule_match:
                    matched_rules.append(rule_match)
                    total_score += rule_match.score
                    all_evidence.extend(rule_match.evidence)
                    self.evaluation_stats["signatures_matched"] += 1

            except Exception as e:
                logger.warning(f"Error evaluating signature {signature.name}: {str(e)}")

        # Cap total score at 100
        total_score = min(100.0, total_score)

        # Always assign "low confidence" for this phase
        confidence_level = "low confidence"

        # Remove duplicate evidence entries
        unique_evidence = list(dict.fromkeys(all_evidence))

        # Determine overall verdict from matched rules
        overall_verdict, not_assessable_reasons = self._compute_overall_verdict(matched_rules, status)

        return RuleEngineResult(
            total_score=total_score,
            confidence_level=confidence_level,
            matched_rules=matched_rules,
            evidence=unique_evidence,
            status=status,
            timestamp=datetime.utcnow().isoformat() + "Z",
            overall_verdict=overall_verdict,
            not_assessable_reasons=not_assessable_reasons
        )

    def _determine_status(self, host_context: Dict[str, Any]) -> str:
        """Determine analysis status based on available data."""
        # Check for key data sources
        has_smb_info = bool(host_context.get("smb_dialects"))
        has_share_info = bool(host_context.get("accessible_shares"))
        has_auth_info = bool(host_context.get("auth_method"))

        if not (has_smb_info or has_share_info or has_auth_info):
            return "insufficient-data"

        return "analyzed"

    def _evaluate_signature(self, signature: Signature, host_context: Dict[str, Any]) -> Optional[RuleMatch]:
        """
        Evaluate a single signature against host context.

        Args:
            signature: Signature to evaluate
            host_context: Host information dictionary

        Returns:
            RuleMatch if signature matches, None otherwise
        """
        matched_conditions = []
        evidence = []

        # Evaluate conditions
        for condition in signature.conditions:
            try:
                if self._evaluate_condition(condition, host_context):
                    matched_conditions.append(condition)
                    evidence.append(f"Condition met: {condition['rationale']}")
                    self.evaluation_stats["conditions_evaluated"] += 1
            except Exception as e:
                logger.debug(f"Error evaluating condition in {signature.name}: {str(e)}")

        # Check if minimum required signals are met
        if len(matched_conditions) < signature.required_signals:
            return None

        # Calculate base score
        score = signature.base_weight

        # Apply boosters
        applied_boosters = []
        for booster in signature.boosters:
            try:
                if self._evaluate_booster(booster, host_context):
                    applied_boosters.append(booster)
                    score += booster.get("weight", 0)
                    evidence.append(f"Booster applied: {booster['description']}")
            except Exception as e:
                logger.debug(f"Error evaluating booster in {signature.name}: {str(e)}")

        # Determine verdict from verdict_mapping
        verdict, not_assessable_reason = self._determine_verdict(signature, host_context)
        if verdict != Verdict.INSUFFICIENT_DATA:
            evidence.append(f"Verdict: {verdict.value}")
            if not_assessable_reason:
                evidence.append(f"Reason: {not_assessable_reason}")

        return RuleMatch(
            signature=signature,
            score=score,
            matched_conditions=matched_conditions,
            applied_boosters=applied_boosters,
            evidence=evidence,
            confidence="low",  # Always low confidence for this phase
            verdict=verdict,
            not_assessable_reason=not_assessable_reason
        )

    def _evaluate_condition(self, condition: Dict[str, Any], host_context: Dict[str, Any]) -> bool:
        """
        Evaluate a single condition against host context.

        Args:
            condition: Condition dictionary from signature
            host_context: Host context data

        Returns:
            True if condition is satisfied
        """
        signal_name = condition.get("signal")
        expectation = condition.get("expectation")

        if not signal_name or expectation is None:
            return False

        # Extract signal value using appropriate extractor
        signal_value = self._extract_signal(signal_name, host_context)

        # Compare with expectation
        return self._compare_signal_value(signal_value, expectation)

    def _evaluate_booster(self, booster: Dict[str, Any], host_context: Dict[str, Any]) -> bool:
        """
        Evaluate a booster condition.

        Boosters add extra weight when certain conditions are met.
        They use the same signal extraction as conditions.

        Args:
            booster: Booster dictionary from signature (may have 'signal' key)
            host_context: Host context data

        Returns:
            True if booster condition is met
        """
        signal_name = booster.get("signal")
        if not signal_name:
            # No signal specified, booster is descriptive only
            return False

        # Extract signal value
        signal_value = self._extract_signal(signal_name, host_context)

        # Check expectation if provided, otherwise check truthiness
        expectation = booster.get("expectation")
        if expectation is not None:
            return self._compare_signal_value(signal_value, expectation)
        else:
            # Default: booster applies if signal is truthy
            return bool(signal_value)

    def _extract_signal(self, signal_name: str, host_context: Dict[str, Any]) -> Any:
        """
        Extract a signal value from host context.

        Args:
            signal_name: Name of the signal to extract
            host_context: Host context data

        Returns:
            Extracted signal value or None if not available
        """
        # Use registered extractor if available
        extractor = self.SIGNAL_EXTRACTORS.get(signal_name)
        if extractor:
            try:
                return extractor(host_context)
            except Exception as e:
                logger.debug(f"Error extracting signal {signal_name}: {str(e)}")
                return None

        # Fallback to direct key lookup with dot notation support
        keys = signal_name.split('.')
        value = host_context

        try:
            for key in keys:
                if isinstance(value, dict):
                    value = value.get(key)
                else:
                    return None
        except Exception:
            return None

        return value

    def _compare_signal_value(self, signal_value: Any, expectation: Any) -> bool:
        """
        Compare extracted signal value with expected value.

        Args:
            signal_value: Extracted value from host context
            expectation: Expected value from signature condition

        Returns:
            True if values match according to comparison logic
        """
        # Handle None/missing values
        if signal_value is None:
            if isinstance(expectation, str) and expectation.lower() == "missing":
                return True
            return expectation is None

        # Direct equality comparison
        if signal_value == expectation:
            return True

        # String-based comparisons
        if isinstance(expectation, str):
            expectation_lower = expectation.lower()
            if expectation_lower == "missing" and not signal_value:
                return True
            if expectation_lower == "present" and bool(signal_value):
                return True
            if isinstance(signal_value, str) and expectation_lower in signal_value.lower():
                return True

        # Boolean comparisons
        if isinstance(expectation, bool):
            return bool(signal_value) == expectation

        return False

    def _determine_verdict(
        self, signature: Signature, host_context: Dict[str, Any]
    ) -> Tuple[Verdict, Optional[str]]:
        """
        Determine verdict for a signature based on verdict_mapping.

        Args:
            signature: Signature with verdict_mapping
            host_context: Host context data

        Returns:
            Tuple of (Verdict, optional reason string)
        """
        vm = signature.verdict_mapping

        # Check confirmed_when first (highest priority)
        for rule in vm.get("confirmed_when", []):
            condition = rule.get("condition", "")
            if self._eval_verdict_condition(condition, host_context):
                return Verdict.CONFIRMED, None

        # Check not_assessable_when
        for rule in vm.get("not_assessable_when", []):
            condition = rule.get("condition", "")
            if self._eval_verdict_condition(condition, host_context):
                return Verdict.NOT_ASSESSABLE, rule.get("reason", "Assessment not possible")

        # Check not_vulnerable_when
        for rule in vm.get("not_vulnerable_when", []):
            condition = rule.get("condition", "")
            if self._eval_verdict_condition(condition, host_context):
                return Verdict.NOT_VULNERABLE, None

        # Check likely_when
        for rule in vm.get("likely_when", []):
            condition = rule.get("condition", "")
            if self._eval_verdict_condition(condition, host_context):
                return Verdict.LIKELY, None

        # Default: if conditions matched but no verdict rule, return LIKELY
        # (since the signature matched)
        return Verdict.LIKELY, None

    def _eval_verdict_condition(self, condition: str, host_context: Dict[str, Any]) -> bool:
        """
        Evaluate a verdict condition string.

        Supports:
        - Equality / inequality: signal == value, signal != value
        - List membership: signal in [v1, v2]
        - Contains: signal contains 'substr'
        - Boolean composition: cond1 AND cond2, cond1 OR cond2
        """
        if not condition:
            return False

        condition = condition.strip()

        if " OR " in condition:
            parts = [p.strip() for p in condition.split(" OR ")]
            return any(self._eval_verdict_condition(p, host_context) for p in parts)

        if " AND " in condition:
            parts = [p.strip() for p in condition.split(" AND ")]
            return all(self._eval_verdict_condition(p, host_context) for p in parts)

        return self._eval_single_condition(condition, host_context)

    def _eval_single_condition(self, condition: str, host_context: Dict[str, Any]) -> bool:
        """Evaluate a single atomic condition."""
        match = re.match(r"(\w+)\s+in\s+\[([^\]]+)\]", condition)
        if match:
            signal_name = match.group(1)
            values = [v.strip().strip("'\"") for v in match.group(2).split(',')]
            signal_value = self._extract_signal(signal_name, host_context)
            return self._value_in_list(signal_value, values)

        match = re.match(r"(\w+)\s*!=\s*['\"]?([^'\"]+)['\"]?", condition)
        if match:
            signal_name, expected = match.groups()
            signal_value = self._extract_signal(signal_name, host_context)
            return not self._compare_values(signal_value, expected.strip())

        match = re.match(r"(\w+)\s*==\s*['\"]?([^'\"]+)['\"]?", condition)
        if match:
            signal_name, expected = match.groups()
            signal_value = self._extract_signal(signal_name, host_context)
            return self._compare_values(signal_value, expected.strip())

        match = re.match(r"(\w+)\s+contains\s+['\"]([^'\"]+)['\"]", condition)
        if match:
            signal_name = match.group(1)
            search_value = match.group(2).lower()
            signal_value = self._extract_signal(signal_name, host_context)
            return self._value_contains(signal_value, search_value)

        logger.debug(f"Could not parse verdict condition: {condition}")
        return False

    def _compare_values(self, signal_value: Any, expected: str) -> bool:
        """Compare signal value with expected, handling type coercion."""
        if expected.lower() == 'true':
            return bool(signal_value)
        if expected.lower() == 'false':
            return not bool(signal_value)

        if signal_value is None:
            return expected.lower() in ('none', 'null', '')

        if expected.lower().startswith('0x'):
            try:
                expected_int = int(expected, 16)
                if isinstance(signal_value, int):
                    return signal_value == expected_int
                if isinstance(signal_value, str):
                    if signal_value.lower().startswith('0x'):
                        return int(signal_value, 16) == expected_int
                    try:
                        return int(signal_value, 16) == expected_int
                    except ValueError:
                        pass
            except ValueError:
                pass

        return str(signal_value).lower() == expected.lower()

    def _value_in_list(self, signal_value: Any, values: List[str]) -> bool:
        """Check if signal value is in list, with type coercion."""
        if signal_value is None:
            return any(v.lower() in ('none', 'null', '') for v in values)

        for v in values:
            if self._compare_values(signal_value, v):
                return True
        return False

    def _value_contains(self, signal_value: Any, search: str) -> bool:
        """Check if signal value contains search string."""
        if signal_value is None:
            return False
        if isinstance(signal_value, str):
            return search in signal_value.lower()
        if isinstance(signal_value, list):
            return any(search in str(v).lower() for v in signal_value)
        return False

    def _compute_overall_verdict(
        self, matched_rules: List[RuleMatch], status: str
    ) -> Tuple[Verdict, List[str]]:
        """
        Compute overall verdict from matched rules.

        Args:
            matched_rules: List of matched rules with verdicts
            status: Analysis status string

        Returns:
            Tuple of (overall Verdict, list of not_assessable reasons)
        """
        if status == "insufficient-data":
            return Verdict.INSUFFICIENT_DATA, []

        if not matched_rules:
            return Verdict.INSUFFICIENT_DATA, ["No signatures matched"]

        # Collect all verdicts and reasons
        verdicts = [rule.verdict for rule in matched_rules]
        reasons = [
            rule.not_assessable_reason
            for rule in matched_rules
            if rule.not_assessable_reason
        ]

        # Highest severity verdict wins
        overall = highest_severity_verdict(verdicts)
        if overall is None:
            overall = Verdict.INSUFFICIENT_DATA

        return overall, reasons

    def get_evaluation_stats(self) -> Dict[str, Any]:
        """Get statistics about rule engine usage."""
        return self.evaluation_stats.copy()

    def get_signature_names(self) -> List[str]:
        """Get list of loaded signature names."""
        return [sig.name for sig in self.signatures]

    def get_signature_count(self) -> int:
        """Get count of loaded signatures."""
        return len(self.signatures)
