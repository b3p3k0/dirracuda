#!/usr/bin/env python3
"""Opt-in API-only catalog checks against an owned search aggregator.

Never follows result URLs or writes the results database. Automated tests
mock all requests.
Uses the application's backend detection, URL construction, and result adapter.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experimental.dorkbook.models import DEFAULT_BUILTIN_DORKS
from experimental.se_dork.backends import is_degoog_endpoint, normalize_results, search_url
from experimental.se_dork.client import run_reachability_check


def catalog_cases(*, all_dorks=False, compare_unquoted=False):
    """Prepare explicit experiments over shipped dorks; never alter saved queries."""
    representative_keys = {
        f"builtin_self_hosted_{key}" for key in ("epub", "movies", "flac", "photos")
    }
    catalog = [d for d in DEFAULT_BUILTIN_DORKS if d.provider == "self_hosted"
               and (all_dorks or d.topic == "General" or d.builtin_key in representative_keys)]
    cases = []
    for dork in catalog:
        cases.append(dict(key=dork.builtin_key, query=dork.query, page=1, variant="shipped"))
        if compare_unquoted and '"' in dork.query:
            cases.append(dict(key=dork.builtin_key, query=dork.query.replace('"', ''),
                              page=1, variant="unquoted_candidate"))
    if catalog:
        cases.append(dict(key=catalog[0].builtin_key, query=catalog[0].query,
                          page=2, variant="shipped"))
    return cases


def directory_title_clues(rows):
    """Count title clues only; this does not verify or fetch directory URLs."""
    return sum(1 for row in rows if isinstance(row, dict)
               and isinstance(row.get("title"), str)
               and re.search(r"\b(?:index of|directory listing)\b", row["title"], re.I))


def upstream_limited(errors):
    """Inspect error fields, not incidental timing/count values such as 429 ms."""
    if isinstance(errors, dict):
        return errors.get("httpStatus") in (429, "429") or any(
            upstream_limited(errors.get(key))
            for key in ("status", "error", "errorReason", "message", "reason")
        )
    if isinstance(errors, (list, tuple)):
        return any(upstream_limited(item) for item in errors)
    if isinstance(errors, str):
        return bool(re.search(r"too many requests|captcha|rate[_ ]limit|\b429\b", errors, re.I))
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-url", required=True, help="Owned SearXNG/DeGoog base URL")
    parser.add_argument("--confirm-live", action="store_true", help="Authorize aggregator requests")
    parser.add_argument("--all", action="store_true", help="Check every shipped web dork")
    parser.add_argument("--compare-unquoted", action="store_true",
                        help="Also test an unquoted candidate for each shipped dork; never saves defaults")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--output", type=Path, required=True, help="JSON report (no target URLs)")
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("Pass --confirm-live to authorize requests to your instance.")
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    detection = run_reachability_check(args.instance_url, args.timeout)
    if not detection.ok:
        print(f"Backend detection failed: {detection.reason_code}: {detection.message}", file=sys.stderr)
        return 1
    endpoint = detection.search_endpoint
    degoog = is_degoog_endpoint(endpoint)
    report = {"date": datetime.now(timezone.utc).isoformat(),
              "backend": "DeGoog" if degoog else "SearXNG", "checks": []}
    cases = catalog_cases(all_dorks=args.all, compare_unquoted=args.compare_unquoted)
    report["planned_checks"] = len(cases)
    report["remaining_checks"] = cases.copy()
    failed = False
    for index, case in enumerate(cases):
        start = time.monotonic()
        check = dict(case)
        stop = False
        try:
            with urllib.request.urlopen(search_url(endpoint, case["query"], case["page"]), timeout=args.timeout) as resp:
                check["http_status"] = resp.status
                payload = json.load(resp)
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValueError("response has no results list")
            rows = normalize_results(payload["results"], degoog=degoog)
            check["results"] = len(rows)
            check["echoed_query"] = payload.get("query")
            check["directory_title_clues"] = directory_title_clues(rows)
            check["yield"] = ("EMPTY" if not rows else "DIRECTORY_TITLE_CLUES"
                              if check["directory_title_clues"] else "NO_DIRECTORY_TITLE_CLUES")
            check["engine_errors"] = []
            for field in ("unresponsive_engines", "errors"):
                errors = payload.get(field) or []
                check["engine_errors"] += errors if isinstance(errors, list) else [errors]
            if degoog:
                timings = payload.get("engineTimings") or []
                if not isinstance(timings, list):
                    raise ValueError("engineTimings must be a list")
                check["engine_errors"] += [
                    item for item in timings
                    if isinstance(item, dict) and (item.get("status") not in (None, "ok")
                                                  or item.get("httpStatus") in (429, "429"))
                ]
            check["engines"] = sorted({engine for row in rows if isinstance(row, dict)
                                      for engine in (row.get("engines") or []) if isinstance(engine, str)})
            check["status"] = "UPSTREAM_ERROR" if check["engine_errors"] else "API_OK"
            failed = failed or bool(check["engine_errors"])
            stop = upstream_limited(check["engine_errors"])
            if stop:
                check["status"] = "UPSTREAM_LIMITED"
                failed = True
        except urllib.error.HTTPError as exc:
            check.update(status="HTTP_ERROR", http_status=exc.code,
                         retry_after=exc.headers.get("Retry-After"))
            failed = True
            # Stop this run on throttling/access denial; never evade upstream limits.
            stop = exc.code in (401, 403, 429)
        except (OSError, ValueError) as exc:
            check.update(status="ERROR", error=type(exc).__name__)
            failed = True
        check["elapsed_seconds"] = round(time.monotonic() - start, 3)
        report["checks"].append(check)
        report["remaining_checks"] = cases[index + 1:]
        report["coverage_complete"] = not report["remaining_checks"]
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(check), flush=True)
        if stop:
            print("Stopped after access/upstream limits; see errors and Retry-After in report.", flush=True)
            break
        time.sleep(2)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
