"""
RCE Scanner Main Interface

Main API for RCE vulnerability scanning. Orchestrates fact collection,
signature evaluation, scoring, and reporting for defensive analysis.
"""

import sys
import os
import logging
from typing import Dict, Any, Optional

# Add signatures package to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from signatures.rce_smb import SignatureLoader, RuleEngine
from .fact_collector import FactCollector
from .scorer import RCEScorer
from .reporter import RCEReporter

logger = logging.getLogger(__name__)


def scan_rce_indicators(host_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main API function for RCE vulnerability scanning.

    Args:
        host_ctx: Host context from probe/access operations containing:
                  - ip_address (required)
                  - auth_method, accessible_shares, smb_dialects (optional)
                  - shodan_data, os_hints, etc. (optional)

    Returns:
        Dictionary with RCE analysis results:
        {
            "score": int (0-100),
            "level": str (risk level + confidence),
            "matched_rules": List[Dict],
            "evidence": List[str],
            "status": str ("analyzed" or "insufficient-data"),
            "timestamp": str (ISO format),
            "analysis_metadata": Dict
        }

    Raises:
        ValueError: If host_ctx is invalid or missing required fields
        RuntimeError: If signature loading or analysis fails
    """
    try:
        # Validate input
        if not isinstance(host_ctx, dict):
            raise ValueError("host_ctx must be a dictionary")

        if not host_ctx.get("ip_address"):
            raise ValueError("host_ctx must include 'ip_address' field")

        logger.debug(f"Starting RCE analysis for {host_ctx.get('ip_address')}")

        # Initialize components
        fact_collector = FactCollector()
        scorer = RCEScorer()
        reporter = RCEReporter()

        # Validate host context has sufficient data
        if not fact_collector.validate_host_context(host_ctx):
            logger.warning(f"Insufficient data for RCE analysis: {host_ctx.get('ip_address')}")
            return _generate_insufficient_data_report(host_ctx)

        # Collect and normalize facts
        host_facts = fact_collector.collect_facts(host_ctx)
        logger.debug(f"Collected {len(host_facts)} facts for analysis")

        # Load signatures (cached after first load)
        signature_loader = _get_signature_loader()
        signatures = signature_loader.load_all_signatures()
        logger.debug(f"Loaded {len(signatures)} signatures for evaluation")

        # Evaluate signatures against host facts
        rule_engine = RuleEngine(signatures)
        rule_result = rule_engine.evaluate_host(host_facts)
        logger.debug(f"Rule evaluation complete: {len(rule_result.matched_rules)} matches")

        # Calculate scores
        scoring_result = scorer.calculate_score(
            [rule.to_dict() for rule in rule_result.matched_rules],
            host_facts
        )
        logger.debug(f"Scoring complete: {scoring_result.total_score}/100")

        # Generate report
        report = reporter.generate_report(
            rule_result.to_dict(),
            scoring_result.__dict__,
            host_facts
        )

        # Validate report structure
        if not reporter.validate_report_structure(report):
            raise RuntimeError("Generated report failed validation")

        logger.info(f"RCE analysis complete for {host_ctx.get('ip_address')}: "
                   f"{report['score']}/100 ({report['level']})")

        return report

    except ValueError as e:
        logger.error(f"Invalid input for RCE scan: {str(e)}")
        raise

    except Exception as e:
        logger.error(f"RCE scan failed for {host_ctx.get('ip_address', 'unknown')}: {str(e)}")
        raise RuntimeError(f"RCE vulnerability scan failed: {str(e)}")


def _generate_insufficient_data_report(host_ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Generate report for cases with insufficient data."""
    from datetime import datetime

    return {
        "score": 0,
        "level": "low (low confidence)",
        "matched_rules": [],
        "evidence": ["Insufficient telemetry data for comprehensive analysis"],
        "status": "insufficient-data",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "analysis_metadata": {
            "confidence": "low confidence",
            "risk_level": "low",
            "score_breakdown": {"insufficient_data": True},
            "facts_analyzed": {"ip_address": host_ctx.get("ip_address", "unknown")}
        }
    }


# Global signature loader instance for caching
_signature_loader_instance = None


def _get_signature_loader() -> SignatureLoader:
    """Get cached signature loader instance."""
    global _signature_loader_instance

    if _signature_loader_instance is None:
        try:
            _signature_loader_instance = SignatureLoader()
            logger.debug("Created new signature loader instance")
        except Exception as e:
            logger.error(f"Failed to create signature loader: {str(e)}")
            raise RuntimeError(f"Signature loader initialization failed: {str(e)}")

    return _signature_loader_instance


def get_scanner_info() -> Dict[str, Any]:
    """
    Get information about the RCE scanner configuration.

    Returns:
        Dictionary with scanner metadata and statistics
    """
    try:
        loader = _get_signature_loader()
        loader.load_all_signatures()

        summary = loader.get_signature_summary()
        load_errors = loader.get_load_errors()

        return {
            "scanner_version": "1.0.0",
            "signatures_loaded": summary.get("total", 0),
            "signature_breakdown": summary.get("by_severity", {}),
            "load_errors_count": len(load_errors),
            "confidence_level": "low confidence",
            "scoring_model": "additive (0-100 scale)"
        }

    except Exception as e:
        logger.error(f"Failed to get scanner info: {str(e)}")
        return {
            "scanner_version": "1.0.0",
            "error": str(e),
            "signatures_loaded": 0
        }


def validate_scanner_setup() -> bool:
    """
    Validate that RCE scanner is properly configured.

    Returns:
        True if scanner can load signatures and run analysis
    """
    try:
        # Test signature loading
        loader = _get_signature_loader()
        signatures = loader.load_all_signatures()

        if len(signatures) == 0:
            logger.error("No signatures loaded - scanner setup invalid")
            return False

        # Test basic analysis with minimal context
        test_context = {"ip_address": "127.0.0.1", "auth_method": "test"}
        result = scan_rce_indicators(test_context)

        if not isinstance(result, dict) or "score" not in result:
            logger.error("Scanner test failed - invalid result format")
            return False

        logger.info(f"Scanner validation passed: {len(signatures)} signatures loaded")
        return True

    except Exception as e:
        logger.error(f"Scanner setup validation failed: {str(e)}")
        return False