"""Pure text formatting helpers for full probe snapshots."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def format_probe_section(
    probe_result: Optional[Dict[str, Any]],
    *,
    show_rce_details: bool = True,
) -> str:
    """Return formatted probe section text for SLB and sidecar detail views."""
    if not probe_result:
        return "🔍 Probe:\n   No probe has been run for this host yet.\n"

    limits = probe_result.get("limits", {})
    max_dirs = limits.get("max_directories")
    max_files = limits.get("max_files")
    timeout = limits.get("timeout_seconds")
    max_depth = limits.get("max_depth")

    lines: List[str] = [
        "🔍 Probe Snapshot:",
        f"   Run: {probe_result.get('run_at', 'Unknown')}",
        f"   Limits: {max_dirs or '?'} dirs / {max_files or '?'} files per share | Timeout: {timeout or '?'}s | Depth: {max_depth or '?'}",
    ]

    shares = probe_result.get("shares", [])
    if shares:
        for share in shares:
            share_name = share.get("share", "Unknown Share")
            lines.append(f"   Share: {share_name}")
            root_files_raw = share.get("root_files", [])
            root_files: List[str] = []
            for item in root_files_raw:
                if isinstance(item, str):
                    name = item.strip()
                elif isinstance(item, dict):
                    name = str(
                        item.get("name")
                        or item.get("file_name")
                        or item.get("file")
                        or ""
                    ).strip()
                else:
                    name = str(item).strip()
                if name:
                    root_files.append(name)

            if root_files:
                lines.append("      Root files:")
                for file_name in root_files:
                    lines.append(f"         • {file_name}")
                if share.get("root_files_truncated"):
                    lines.append("         … additional root files not shown")
            directories = share.get("directories", [])
            if not directories:
                if not root_files:
                    lines.append("      (no directories returned)")
            for directory in directories:
                dir_name = directory.get("name", "")
                lines.append(f"      📁 {dir_name}/")
                tree = _build_probe_directory_tree(
                    directory.get("subdirectories", []),
                    directory.get("files", []),
                )
                if _render_probe_directory_tree(tree, lines, indent=9) == 0:
                    lines.append("         (no files or subdirectories listed)")
                if directory.get("subdirectories_truncated"):
                    lines.append("         … additional subdirectories not shown")
                if directory.get("files_truncated"):
                    lines.append("         … additional files not shown")
            if share.get("directories_truncated"):
                lines.append("      … additional directories not shown")
    else:
        lines.append("   No shares were successfully probed.")

    if show_rce_details:
        rce_lines = _format_rce_summary(probe_result.get("rce_analysis"))
        if rce_lines:
            lines.append("")
            lines.extend(rce_lines)

    analysis = probe_result.get("indicator_analysis") if probe_result else None
    if analysis:
        matches = analysis.get("matches", [])
        if matches:
            lines.append("\n   ☠ Indicators Detected:")
            for match in matches[:5]:
                indicator = match.get("indicator", "Indicator")
                path = match.get("path", "(unknown path)")
                lines.append(f"      {indicator} → {path}")
            if len(matches) > 5:
                lines.append(f"      … {len(matches) - 5} additional hits")
        else:
            lines.append("\n   ✅ No ransomware indicators detected in sampled paths.")

    errors = probe_result.get("errors", [])
    if errors:
        lines.append("\n   ⚠ Probe Errors:")
        for err in errors:
            if isinstance(err, dict):
                share = err.get("share", "Unknown share")
                message = err.get("message", "Unknown error")
            else:
                share = "Unknown share"
                message = str(err)
            lines.append(f"      {share}: {message}")

    lines.append("")
    return "\n".join(lines)


def _normalize_probe_relative_path(path_value: Any) -> List[str]:
    """Return normalized path segments for a relative probe entry."""
    text = str(path_value or "").strip().replace("\\", "/")
    if not text:
        return []
    return [segment for segment in text.split("/") if segment and segment != "."]


def _build_probe_directory_tree(subdirectories: Any, files: Any) -> Dict[str, Any]:
    """Build a de-duplicated insertion-ordered tree from relative probe paths."""
    root: Dict[str, Any] = {"dirs": {}, "files": []}

    for raw_subdir in subdirectories or []:
        segments = _normalize_probe_relative_path(raw_subdir)
        if not segments:
            continue
        node = root
        for segment in segments:
            child = node["dirs"].get(segment)
            if child is None:
                child = {"dirs": {}, "files": []}
                node["dirs"][segment] = child
            node = child

    for raw_file in files or []:
        segments = _normalize_probe_relative_path(raw_file)
        if not segments:
            continue
        *folder_segments, file_name = segments
        node = root
        for segment in folder_segments:
            child = node["dirs"].get(segment)
            if child is None:
                child = {"dirs": {}, "files": []}
                node["dirs"][segment] = child
            node = child
        if file_name not in node["files"]:
            node["files"].append(file_name)

    return root


def _render_probe_directory_tree(tree: Dict[str, Any], lines: List[str], indent: int) -> int:
    """Render a nested tree and return count of lines emitted."""
    emitted = 0
    prefix = " " * indent
    for dir_name, child in tree["dirs"].items():
        lines.append(f"{prefix}📁 {dir_name}/")
        emitted += 1
        emitted += _render_probe_directory_tree(child, lines, indent + 3)
    for file_name in tree["files"]:
        lines.append(f"{prefix}• {file_name}")
        emitted += 1
    return emitted


def _format_rce_summary(rce_report: Optional[Dict[str, Any]]) -> List[str]:
    """Generate formatted RCE analysis summary lines with verdict information."""
    prefix = "   RCE Vuln scan:"

    if rce_report is None:
        return [f"{prefix} not requested"]

    if not isinstance(rce_report, dict):
        return [f"{prefix} unavailable"]

    status = (rce_report.get("status") or "").lower()
    error_message = rce_report.get("error")

    if status in {"scanner-unavailable", "analysis-failed"}:
        reason = error_message or ("scanner unavailable" if status == "scanner-unavailable" else "analysis failed")
        return [f"{prefix} unavailable – {reason}"]

    verdict = rce_report.get("verdict", "")
    not_assessable_reason = rce_report.get("not_assessable_reason")

    if verdict == "insufficient_data" or status == "insufficient-data":
        return [f"{prefix} INSUFFICIENT DATA; no verdict"]

    if verdict == "not_assessable":
        reason = not_assessable_reason or "Assessment not possible"
        return [f"{prefix} NOT ASSESSABLE – {reason}"]

    if verdict == "not_vulnerable":
        return [f"{prefix} NOT VULNERABLE"]

    if verdict == "error":
        return [f"{prefix} ERROR – {error_message or 'analysis failed'}"]

    score = rce_report.get("score", 0)
    verdict_display = verdict.upper().replace("_", " ") if verdict else "UNKNOWN"

    findings = rce_report.get("findings") or []
    matched_rules = rce_report.get("matched_rules") or []

    if findings:
        lines = [f"{prefix} {verdict_display} (score {score}/100)"]
        for finding in findings[:3]:
            cve_ids = finding.get("cve_ids", ["?"])
            cve = cve_ids[0] if cve_ids else "?"
            name = finding.get("name", "Unknown")
            finding_verdict = finding.get("verdict", "unknown").upper()
            severity = (finding.get("severity") or "unknown").lower()
            lines.append(f"      • {cve} ({name}): {finding_verdict} ({severity})")
            if finding.get("not_assessable_reason"):
                lines.append(f"        Reason: {finding['not_assessable_reason']}")

        if len(findings) > 3:
            lines.append(f"      … {len(findings) - 3} additional findings")

        return lines

    if matched_rules:
        sorted_rules = sorted(matched_rules, key=lambda rule: rule.get("score", 0), reverse=True)
        primary_rule = sorted_rules[0]
        primary_label = _format_rce_rule_label(primary_rule)
        rule_verdict = primary_rule.get("verdict", verdict_display).upper()

        lines = [f"{prefix} {rule_verdict} – {primary_label} (score {score}/100)"]
        for rule in sorted_rules[:3]:
            label = _format_rce_rule_label(rule)
            severity = (rule.get("severity") or "unknown").lower()
            rule_score = rule.get("score", 0)
            rv = rule.get("verdict", "unknown").upper()
            lines.append(f"      • {label}: {rv} ({severity} severity, +{rule_score})")

        if len(sorted_rules) > 3:
            lines.append(f"      … {len(sorted_rules) - 3} additional signatures")

        return lines

    if verdict in ("confirmed", "likely"):
        return [f"{prefix} {verdict_display} (score {score}/100)"]

    return [f"{prefix} none found (score {score}/100)"]


def _format_rce_rule_label(rule: Dict[str, Any]) -> str:
    """Return friendly label combining rule name and primary CVE."""
    name = rule.get("name") or "Unknown"
    cve_ids = rule.get("cve_ids") or []
    if cve_ids:
        return f"{name} ({cve_ids[0]})"
    return name
