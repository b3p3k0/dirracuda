"""
RCE Reporter

Reporting and formatting for RCE vulnerability assessment results.
Provides structured output compatible with existing probe and access workflows.

Supports the verdict taxonomy:
- CONFIRMED: Safe evidence proves vulnerability
- LIKELY: Exposure supported but not provable safely
- NOT_VULNERABLE: Signal truly excludes vulnerability
- NOT_ASSESSABLE: Needs auth or intrusive probe
- INSUFFICIENT_DATA: Not enough telemetry
- ERROR: Probe/analysis failed
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, UTC

from .verdicts import Verdict, verdict_to_rce_status, highest_severity_verdict

logger = logging.getLogger(__name__)


class RCEReporter:
    """
    RCE analysis results reporter.

    Formats RCE analysis results into structured output compatible
    with existing probe cache and display systems.
    """

    def __init__(self):
        """Initialize RCE reporter."""
        self.report_stats = {
            "reports_generated": 0,
            "by_confidence": {"low confidence": 0},
            "by_status": {"analyzed": 0, "insufficient-data": 0}
        }

    def generate_report(self, rule_engine_result: Dict[str, Any],
                       scoring_result: Dict[str, Any],
                       host_facts: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate structured RCE analysis report.

        Args:
            rule_engine_result: Results from rule engine evaluation
            scoring_result: Results from RCE scoring
            host_facts: Normalized host facts

        Returns:
            Structured report dictionary for probe cache storage
        """
        self.report_stats["reports_generated"] += 1

        # Extract key information
        total_score = scoring_result.get("total_score", 0)
        confidence_level = scoring_result.get("confidence_level", "low confidence")
        risk_assessment = scoring_result.get("risk_assessment", "low")
        status = rule_engine_result.get("status", "analyzed")

        # Update statistics
        self.report_stats["by_confidence"][confidence_level] = (
            self.report_stats["by_confidence"].get(confidence_level, 0) + 1
        )
        self.report_stats["by_status"][status] = (
            self.report_stats["by_status"].get(status, 0) + 1
        )

        # Determine overall verdict from rule engine result or matched rules
        matched_rules = rule_engine_result.get("matched_rules", [])
        overall_verdict = self._determine_overall_verdict(rule_engine_result, matched_rules)
        not_assessable_reasons = self._collect_not_assessable_reasons(matched_rules)

        # Build main report structure with new verdict fields
        report = {
            # New primary verdict fields
            "verdict": overall_verdict.value,
            "not_assessable_reason": not_assessable_reasons[0] if not_assessable_reasons else None,
            "findings": self._format_findings(matched_rules),
            "rce_status": verdict_to_rce_status(overall_verdict),

            # Backward compatibility fields
            "score": total_score,
            "level": f"{risk_assessment} ({confidence_level})",
            "matched_rules": self._format_matched_rules(matched_rules),
            "evidence": self._format_evidence(rule_engine_result.get("evidence", [])),
            "status": status,
            "timestamp": rule_engine_result.get(
                "timestamp",
                datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            ),
            "analysis_metadata": {
                "confidence": confidence_level,
                "risk_level": risk_assessment,
                "score_breakdown": scoring_result.get("score_breakdown", {}),
                "facts_analyzed": self._get_facts_summary(host_facts),
                "not_assessable_reasons": not_assessable_reasons
            }
        }

        return report

    def _determine_overall_verdict(self, rule_engine_result: Dict[str, Any],
                                   matched_rules: List[Any]) -> Verdict:
        """
        Determine overall verdict from rule engine result.

        Priority: CONFIRMED > LIKELY > NOT_ASSESSABLE > NOT_VULNERABLE > INSUFFICIENT_DATA > ERROR

        Args:
            rule_engine_result: Full rule engine result (may contain overall_verdict)
            matched_rules: List of matched rule objects or dicts

        Returns:
            Overall Verdict for the host
        """
        # Check if rule engine already computed overall verdict
        if "verdict" in rule_engine_result:
            verdict_str = rule_engine_result["verdict"]
            try:
                return Verdict(verdict_str)
            except ValueError:
                pass

        # Check status for error/insufficient data conditions
        status = rule_engine_result.get("status", "analyzed")
        if status == "insufficient-data":
            return Verdict.INSUFFICIENT_DATA

        if not matched_rules:
            return Verdict.INSUFFICIENT_DATA

        # Collect verdicts from matched rules
        verdicts = []
        for rule in matched_rules:
            if hasattr(rule, 'verdict'):
                verdicts.append(rule.verdict)
            elif isinstance(rule, dict) and "verdict" in rule:
                verdict_str = rule["verdict"]
                try:
                    verdicts.append(Verdict(verdict_str))
                except ValueError:
                    pass

        if not verdicts:
            return Verdict.INSUFFICIENT_DATA

        # Highest severity verdict wins
        overall = highest_severity_verdict(verdicts)
        return overall if overall else Verdict.INSUFFICIENT_DATA

    def _collect_not_assessable_reasons(self, matched_rules: List[Any]) -> List[str]:
        """
        Collect all not_assessable reasons from matched rules.

        Args:
            matched_rules: List of matched rule objects or dicts

        Returns:
            List of reason strings
        """
        reasons = []
        for rule in matched_rules:
            reason = None
            if hasattr(rule, 'not_assessable_reason'):
                reason = rule.not_assessable_reason
            elif isinstance(rule, dict):
                reason = rule.get("not_assessable_reason")

            if reason and reason not in reasons:
                reasons.append(reason)

        return reasons

    def _format_findings(self, matched_rules: List[Any]) -> List[Dict[str, Any]]:
        """
        Format matched rules as findings with verdict information.

        Args:
            matched_rules: List of matched rule objects or dicts

        Returns:
            List of finding dictionaries
        """
        findings = []
        for rule in matched_rules:
            if hasattr(rule, 'signature'):
                # RuleMatch object
                finding = {
                    "cve_ids": rule.signature.cve_ids,
                    "name": rule.signature.name,
                    "severity": rule.signature.severity,
                    "verdict": rule.verdict.value if hasattr(rule.verdict, 'value') else str(rule.verdict),
                    "score": rule.score,
                    "evidence": rule.evidence[:3] if rule.evidence else []
                }
                if rule.not_assessable_reason:
                    finding["not_assessable_reason"] = rule.not_assessable_reason
            elif isinstance(rule, dict):
                # Dict from serialization
                finding = {
                    "cve_ids": rule.get("cve_ids", []),
                    "name": rule.get("signature_name", "Unknown"),
                    "severity": rule.get("severity", "unknown"),
                    "verdict": rule.get("verdict", "insufficient_data"),
                    "score": rule.get("score", 0),
                    "evidence": rule.get("evidence", [])[:3]
                }
                if rule.get("not_assessable_reason"):
                    finding["not_assessable_reason"] = rule["not_assessable_reason"]
            else:
                continue

            findings.append(finding)

        return findings

    def _format_matched_rules(self, matched_rules: List[Any]) -> List[Dict[str, Any]]:
        """Format matched rules for report output."""
        formatted_rules = []

        for rule in matched_rules:
            # Handle RuleMatch objects
            if hasattr(rule, 'signature'):
                formatted_rule = {
                    "name": rule.signature.name,
                    "cve_ids": rule.signature.cve_ids,
                    "score": rule.score,
                    "severity": rule.signature.severity,
                    "verdict": rule.verdict.value if hasattr(rule.verdict, 'value') else str(rule.verdict),
                    "conditions_met": len(rule.matched_conditions),
                    "boosters_applied": len(rule.applied_boosters)
                }
                if rule.not_assessable_reason:
                    formatted_rule["not_assessable_reason"] = rule.not_assessable_reason

                # Include specific conditions that matched
                if rule.matched_conditions:
                    formatted_rule["conditions"] = [
                        {
                            "signal": cond.get("signal", ""),
                            "rationale": cond.get("rationale", "")
                        }
                        for cond in rule.matched_conditions if isinstance(cond, dict)
                    ]

                formatted_rules.append(formatted_rule)

            # Handle dict format (from serialization)
            elif isinstance(rule, dict):
                formatted_rule = {
                    "name": rule.get("signature_name", "Unknown"),
                    "cve_ids": rule.get("cve_ids", []),
                    "score": rule.get("score", 0),
                    "severity": rule.get("severity", "unknown"),
                    "verdict": rule.get("verdict", "insufficient_data"),
                    "conditions_met": len(rule.get("matched_conditions", [])),
                    "boosters_applied": len(rule.get("applied_boosters", []))
                }
                if rule.get("not_assessable_reason"):
                    formatted_rule["not_assessable_reason"] = rule["not_assessable_reason"]

                # Include specific conditions that matched
                conditions = rule.get("matched_conditions", [])
                if conditions:
                    formatted_rule["conditions"] = [
                        {
                            "signal": cond.get("signal", ""),
                            "rationale": cond.get("rationale", "")
                        }
                        for cond in conditions if isinstance(cond, dict)
                    ]

                formatted_rules.append(formatted_rule)

        return formatted_rules

    def _format_evidence(self, evidence: List[str]) -> List[str]:
        """Format evidence list for report output."""
        if not isinstance(evidence, list):
            return []

        # Filter and clean evidence entries
        formatted_evidence = []
        seen_evidence = set()

        for item in evidence:
            if isinstance(item, str) and item.strip():
                clean_evidence = item.strip()
                if clean_evidence not in seen_evidence:
                    formatted_evidence.append(clean_evidence)
                    seen_evidence.add(clean_evidence)

        return formatted_evidence

    def _get_facts_summary(self, host_facts: Dict[str, Any]) -> Dict[str, Any]:
        """Create summary of facts used in analysis."""
        summary = {}

        # Key facts for transparency (including new probe-derived facts)
        fact_keys = [
            "ip_address", "supports_smb1", "supports_smb2", "supports_smb3",
            "anonymous_access", "has_admin_access", "has_accessible_shares",
            "detected_os", "known_vulns_count",
            # New probe-derived facts
            "smb1_possible", "has_compression", "signing_required",
            "smb_dialect", "ms17_010_status",
            # Implementation markers
            "ksmbd_marker", "samba_marker", "shodan_os",
            # Domain role indicators
            "has_domain_role_indicators", "has_netlogon_share", "has_sysvol_share",
            "has_print_share", "ipc_share_accessible"
        ]

        for key in fact_keys:
            if key in host_facts:
                summary[key] = host_facts[key]

        # Count-based summaries
        summary["accessible_shares_count"] = len(host_facts.get("accessible_shares", []))
        summary["admin_shares_count"] = len(host_facts.get("admin_shares_accessible", []))
        summary["shodan_ports_count"] = len(host_facts.get("shodan_ports", []))
        summary["compression_algos_count"] = len(host_facts.get("compression_algos", []))

        return summary

    def generate_summary_text(self, report: Dict[str, Any]) -> str:
        """
        Generate human-readable summary text for display.

        Args:
            report: RCE analysis report dictionary

        Returns:
            Formatted summary text
        """
        verdict = report.get("verdict", "insufficient_data")
        score = report.get("score", 0)
        level = report.get("level", "unknown")
        matched_count = len(report.get("matched_rules", []))
        not_assessable_reason = report.get("not_assessable_reason")

        # Verdict-first display
        verdict_upper = verdict.upper().replace("_", " ")

        if verdict == "insufficient_data":
            return f"RCE Analysis: {verdict_upper} - Limited data available ({score}/100)"

        if verdict == "not_assessable" and not_assessable_reason:
            return f"RCE Analysis: {verdict_upper} - {not_assessable_reason}"

        if verdict == "not_vulnerable":
            return f"RCE Analysis: {verdict_upper} ({score}/100)"

        if matched_count == 0:
            return f"RCE Analysis: {verdict_upper} ({score}/100) - No specific vulnerabilities detected"

        rule_names = [rule.get("name", "Unknown") for rule in report.get("matched_rules", [])]
        if len(rule_names) == 1:
            return f"RCE Analysis: {verdict_upper} - Potential {rule_names[0]} ({score}/100)"
        else:
            return f"RCE Analysis: {verdict_upper} - {matched_count} potential vulnerabilities ({score}/100)"

    def generate_verbose_text(self, report: Dict[str, Any]) -> List[str]:
        """
        Generate verbose analysis text for detailed output.

        Args:
            report: RCE analysis report dictionary

        Returns:
            List of formatted text lines for verbose display
        """
        lines = []

        # Header with verdict
        verdict = report.get("verdict", "insufficient_data")
        score = report.get("score", 0)
        level = report.get("level", "unknown")
        verdict_upper = verdict.upper().replace("_", " ")
        lines.append(f"RCE Vulnerability Analysis: {verdict_upper}")
        lines.append(f"  Score: {score}/100 ({level})")

        # Not assessable reason if present
        not_assessable_reason = report.get("not_assessable_reason")
        if not_assessable_reason:
            lines.append(f"  Not Assessable: {not_assessable_reason}")

        # Status
        status = report.get("status", "analyzed")
        if status == "insufficient-data":
            lines.append("  Status: Analysis limited by insufficient telemetry data")
        else:
            lines.append(f"  Status: {status.title()}")

        # Findings (new format with verdict per finding)
        findings = report.get("findings", [])
        if findings:
            lines.append(f"  Findings: {len(findings)}")
            for finding in findings[:3]:  # Show first 3
                cve_ids = finding.get("cve_ids", ["?"])
                cve = cve_ids[0] if cve_ids else "?"
                name = finding.get("name", "Unknown")
                finding_verdict = finding.get("verdict", "unknown")
                severity = finding.get("severity", "unknown")
                lines.append(f"    - {cve} ({name}): {finding_verdict.upper()} ({severity})")
                if finding.get("not_assessable_reason"):
                    lines.append(f"        Reason: {finding['not_assessable_reason']}")

            if len(findings) > 3:
                lines.append(f"    ... and {len(findings) - 3} more")

        # Matched rules (backward compat)
        matched_rules = report.get("matched_rules", [])
        if matched_rules and not findings:
            lines.append(f"  Matched Signatures: {len(matched_rules)}")
            for rule in matched_rules[:3]:  # Show first 3
                name = rule.get("name", "Unknown")
                rule_score = rule.get("score", 0)
                severity = rule.get("severity", "unknown")
                rule_verdict = rule.get("verdict", "unknown")
                lines.append(f"    - {name}: {rule_verdict.upper()} ({rule_score} pts, {severity})")

            if len(matched_rules) > 3:
                lines.append(f"    ... and {len(matched_rules) - 3} more")

        # Evidence (top few items)
        evidence = report.get("evidence", [])
        if evidence:
            lines.append(f"  Evidence: {len(evidence)} indicators")
            for item in evidence[:2]:  # Show first 2
                lines.append(f"    - {item}")

            if len(evidence) > 2:
                lines.append(f"    ... and {len(evidence) - 2} more indicators")

        # Facts summary
        facts = report.get("analysis_metadata", {}).get("facts_analyzed", {})
        if facts:
            key_facts = []
            if facts.get("supports_smb1") or facts.get("smb1_possible"):
                key_facts.append("SMB1 enabled")
            if facts.get("anonymous_access"):
                key_facts.append("anonymous access")
            if facts.get("has_admin_access"):
                key_facts.append("admin shares accessible")
            if facts.get("has_compression"):
                key_facts.append("SMB3 compression")

            if key_facts:
                lines.append(f"  Key Risk Factors: {', '.join(key_facts)}")

        return lines

    def get_report_stats(self) -> Dict[str, Any]:
        """Get statistics about generated reports."""
        return self.report_stats.copy()

    def reset_stats(self) -> None:
        """Reset reporting statistics."""
        self.report_stats = {
            "reports_generated": 0,
            "by_confidence": {"low confidence": 0},
            "by_status": {"analyzed": 0, "insufficient-data": 0}
        }

    def validate_report_structure(self, report: Dict[str, Any]) -> bool:
        """
        Validate report structure meets expected format.

        Args:
            report: Generated report dictionary

        Returns:
            True if report structure is valid
        """
        # Required keys including new verdict field
        required_keys = [
            "verdict", "score", "level", "matched_rules", "evidence",
            "status", "timestamp", "rce_status"
        ]

        for key in required_keys:
            if key not in report:
                return False

        # Validate verdict is valid enum value
        verdict = report.get("verdict")
        valid_verdicts = [
            "confirmed", "likely", "not_vulnerable",
            "not_assessable", "insufficient_data", "error"
        ]
        if verdict not in valid_verdicts:
            return False

        # Validate rce_status
        rce_status = report.get("rce_status")
        valid_statuses = ["not_run", "clean", "flagged", "unknown", "error"]
        if rce_status not in valid_statuses:
            return False

        # Validate score is integer 0-100
        score = report.get("score")
        if not isinstance(score, int) or not (0 <= score <= 100):
            return False

        # Validate lists
        if not isinstance(report.get("matched_rules"), list):
            return False

        if not isinstance(report.get("evidence"), list):
            return False

        if not isinstance(report.get("findings"), list):
            return False

        return True
