#!/usr/bin/env python3
"""SMB Shodan credit lab: compare discovery-yield strategies side-by-side."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import shodan

# Allow direct script execution: `python tools/smb_credit_lab.py ...`
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from commands.discover import host_filter, shodan_query
from shared.config import load_config
from shared.database import SMBSeekWorkflowDatabase
from shared.output import create_output_manager


def _coerce_int(value: Any, default: int, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    return parsed


def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


@dataclass(frozen=True)
class StrategySpec:
    name: str
    credit_budget: Optional[int]
    adaptive_target: Optional[int]
    reference_current: bool = False


STRATEGIES: Dict[str, StrategySpec] = {
    "strict_1_credit": StrategySpec(name="strict_1_credit", credit_budget=1, adaptive_target=50),
    "adaptive_2_credit": StrategySpec(name="adaptive_2_credit", credit_budget=2, adaptive_target=50),
    "adaptive_3_credit": StrategySpec(name="adaptive_3_credit", credit_budget=3, adaptive_target=50),
    "reference_current": StrategySpec(name="reference_current", credit_budget=None, adaptive_target=None, reference_current=True),
}


class _LabOperation:
    def __init__(self, config, output) -> None:
        self.config = config
        self.output = output
        self.shodan_api = shodan.Shodan(config.get_shodan_api_key())
        self.shodan_host_metadata: Dict[str, Dict[str, Any]] = {}
        self._host_lookup_cache: Dict[str, Any] = {}
        self._auth_method_cache: Dict[str, str] = {}
        self.stats: Dict[str, Any] = {}
        self.exclusions = host_filter.load_exclusions(self)



def _build_query(op: _LabOperation, country: Optional[str], custom_filters: str) -> str:
    countries = op.config.resolve_target_countries(country)
    return shodan_query.build_targeted_query(op, countries, custom_filters)


def _build_query_limits(base_shodan_cfg: Dict[str, Any], spec: StrategySpec) -> Dict[str, int]:
    q_limits = dict(base_shodan_cfg.get("query_limits", {}))
    max_results = _coerce_int(q_limits.get("max_results"), 1000)

    if spec.reference_current:
        # Reference path: emulate configured max-results behavior without an
        # additional strict credit cap from this lab harness.
        q_limits["smb_max_query_credits_per_scan"] = max(1, _ceil_div(max_results, 100))
        q_limits["max_query_credits_per_scan"] = q_limits["smb_max_query_credits_per_scan"]
        q_limits["min_usable_hosts_target"] = max_results + 1
    else:
        q_limits["smb_max_query_credits_per_scan"] = spec.credit_budget
        q_limits["max_query_credits_per_scan"] = spec.credit_budget
        if spec.adaptive_target is not None:
            q_limits["min_usable_hosts_target"] = spec.adaptive_target

    return shodan_query._resolve_query_limits({"query_limits": q_limits})


def _extract_ips(matches: List[dict]) -> Set[str]:
    ips: Set[str] = set()
    for result in matches:
        ip = result.get("ip_str")
        if isinstance(ip, str) and ip:
            ips.add(ip)
    return ips


def _run_strategy(
    op: _LabOperation,
    database: SMBSeekWorkflowDatabase,
    query: str,
    spec: StrategySpec,
    *,
    rescan_all: bool,
    rescan_failed: bool,
) -> Dict[str, Any]:
    op.shodan_host_metadata = {}
    op._host_lookup_cache = {}
    op._auth_method_cache = {}
    op.stats = {}

    shodan_cfg = op.config.get_shodan_config()
    query_limits = _build_query_limits(shodan_cfg, spec)

    matches = shodan_query._collect_shodan_matches(op, query, query_limits)
    raw_ips = _extract_ips(matches)
    filtered_ips = host_filter.apply_exclusions(op, raw_ips)
    to_scan, _filter_stats = database.get_new_hosts_filter(
        filtered_ips,
        rescan_all=rescan_all,
        rescan_failed=rescan_failed,
        output_manager=None,
    )

    estimated_credits = max(1, _coerce_int(op.stats.get("shodan_pages_fetched"), 1))
    usable_count = len(to_scan)
    usable_per_credit = round(usable_count / estimated_credits, 2)

    return {
        "strategy": spec.name,
        "max_results": query_limits["max_results"],
        "credit_budget": query_limits["max_query_credits_per_scan"],
        "adaptive_target": query_limits["min_usable_hosts_target"],
        "effective_limit": query_limits["effective_limit"],
        "raw_matches": len(raw_ips),
        "post_exclusion": len(filtered_ips),
        "post_recent_filter": usable_count,
        "estimated_credits": estimated_credits,
        "usable_per_credit": usable_per_credit,
    }


def _print_table(rows: List[Dict[str, Any]]) -> None:
    headers = [
        "strategy",
        "credits",
        "raw",
        "post_excl",
        "usable",
        "usable/credit",
        "effective_limit",
    ]

    width_map = {key: len(key) for key in headers}
    table_rows: List[Dict[str, str]] = []

    for row in rows:
        rendered = {
            "strategy": str(row["strategy"]),
            "credits": str(row["estimated_credits"]),
            "raw": str(row["raw_matches"]),
            "post_excl": str(row["post_exclusion"]),
            "usable": str(row["post_recent_filter"]),
            "usable/credit": str(row["usable_per_credit"]),
            "effective_limit": str(row["effective_limit"]),
        }
        for key, value in rendered.items():
            width_map[key] = max(width_map[key], len(value))
        table_rows.append(rendered)

    def _fmt_line(values: Dict[str, str]) -> str:
        return " | ".join(values[key].ljust(width_map[key]) for key in headers)

    header_line = _fmt_line({key: key for key in headers})
    separator = "-+-".join("-" * width_map[key] for key in headers)
    print(header_line)
    print(separator)
    for row in table_rows:
        print(_fmt_line(row))



def _save_artifact(payload: Dict[str, Any]) -> Path:
    root = Path.home() / ".dirracuda" / "state" / "benchmarks"
    root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    output_path = root / f"smb_credit_lab_{ts}.json"
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_path



def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark SMB Shodan credit/yield strategies")
    parser.add_argument("--config", default=None, help="Path to config file (defaults to runtime config)")
    parser.add_argument("--country", default=None, help="Country code(s), comma-separated, e.g. US or US,CA")
    parser.add_argument("--filter", default="", help="Custom Shodan filter suffix")
    parser.add_argument(
        "--strategy",
        default="all",
        choices=["all", *STRATEGIES.keys()],
        help="Benchmark one strategy or all",
    )
    parser.add_argument("--rescan-all", action="store_true", help="Bypass recent-host filter")
    parser.add_argument("--rescan-failed", action="store_true", help="Retry recent failed hosts")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    return parser.parse_args()



def main() -> int:
    args = _parse_args()

    config = load_config(args.config)
    output = create_output_manager(config, quiet=False, verbose=args.verbose, no_colors=True)

    try:
        op = _LabOperation(config, output)
    except Exception as exc:
        print(f"ERROR: failed to initialize Shodan client: {exc}")
        return 1

    database = SMBSeekWorkflowDatabase(config, verbose=bool(args.verbose))
    query = _build_query(op, args.country, args.filter)

    if args.strategy == "all":
        strategy_list = [
            STRATEGIES["strict_1_credit"],
            STRATEGIES["adaptive_2_credit"],
            STRATEGIES["adaptive_3_credit"],
            STRATEGIES["reference_current"],
        ]
    else:
        strategy_list = [STRATEGIES[args.strategy]]

    rows: List[Dict[str, Any]] = []
    for spec in strategy_list:
        rows.append(
            _run_strategy(
                op,
                database,
                query,
                spec,
                rescan_all=bool(args.rescan_all),
                rescan_failed=bool(args.rescan_failed),
            )
        )

    _print_table(rows)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "query": query,
        "country": args.country,
        "custom_filter": args.filter,
        "rescan_all": bool(args.rescan_all),
        "rescan_failed": bool(args.rescan_failed),
        "rows": rows,
    }
    artifact_path = _save_artifact(payload)
    print(f"\nSaved benchmark artifact: {artifact_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
