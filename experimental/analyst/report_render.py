"""Pure on-demand renderers for validated read-first Analyst reports."""

from __future__ import annotations

import csv
import html
import io
import re
from collections.abc import Callable

from .report_contract import HTML_CSP, csv_safe
from .report_json import (
    UNVERIFIED_NOTICE,
    fact_rank_label,
    fact_seen_label,
    validate_report_json,
)


_BACKTICK_RUN = re.compile(r"`+")


def render_markdown(report: dict) -> str:
    """Render a validated report as the primary read-first Markdown view."""
    validate_report_json(report)
    run = report["run"]
    read = report["read"]
    facts = report["facts"]

    lines = [
        "# WHAT THIS IS",
        "",
        f"Risk: ● {_markdown(read['risk_level'])}",
        "",
        _markdown(read["host_summary"]),
        "",
        f"Likely owner : {_markdown_optional(read['likely_owner'])}",
        f"Contacts     : {_markdown_contacts(read['contacts'])}",
        (
            f"Files read   : {_markdown(run['files_read'])}      "
            f"Flagged files: {_markdown(run['flagged_files'])}"
        ),
        "",
        "## TOP EXPOSURES",
        "",
    ]
    exposures = [
        exposure for exposure in read["top_exposures"]
        if exposure["severity"] != "LOW"
    ]
    if exposures:
        lines.extend(
            f"{_markdown(item['rank'])}. {_markdown(item['severity'])}  "
            f"{_markdown(item['text'])}"
            for item in exposures
        )
    else:
        lines.append("(none)")
    lines.extend([
        "",
        UNVERIFIED_NOTICE,
        "",
        "## AFFILIATIONS",
        "",
    ])
    lines.extend(_affiliation_markdown(report))
    lines.extend([
        "",
        "## FACTS",
        "",
        "| Kind | Value | File | Seen | Rank |",
        "|---|---|---|---|---|",
    ])
    lines.extend(
        "| "
        + " | ".join((
            _markdown_table(fact["kind"]),
            _markdown_code(fact["quote"]),
            _markdown_table(fact["file"]),
            _markdown_table(fact_seen_label(fact)),
            _markdown_table(fact_rank_label(fact)),
        ))
        + " |"
        for fact in facts
    )
    return "\n".join(lines) + "\n"


def render_text(report: dict) -> str:
    """Render a validated report as a read-first plain-text document."""
    validate_report_json(report)
    run = report["run"]
    read = report["read"]
    facts = report["facts"]

    lines = [
        "WHAT THIS IS",
        f"Risk: ● {_plain(read['risk_level'])}",
        "",
        _plain(read["host_summary"]),
        "",
        f"Likely owner : {_plain_optional(read['likely_owner'])}",
        f"Contacts     : {_plain_contacts(read['contacts'])}",
        (
            f"Files read   : {_plain(run['files_read'])}      "
            f"Flagged files: {_plain(run['flagged_files'])}"
        ),
        "",
        "TOP EXPOSURES",
    ]
    exposures = [
        exposure for exposure in read["top_exposures"]
        if exposure["severity"] != "LOW"
    ]
    if exposures:
        lines.extend(
            f"{_plain(item['rank'])}. {_plain(item['severity'])}  "
            f"{_plain(item['text'])}"
            for item in exposures
        )
    else:
        lines.append("(none)")
    lines.extend([
        "", UNVERIFIED_NOTICE, "", "AFFILIATIONS",
    ])
    lines.extend(_affiliation_text(report))
    lines.extend([
        "", "FACTS",
        "Kind | Value | File | Seen | Rank",
    ])
    lines.extend(
        " | ".join((
            _plain(fact["kind"]),
            _plain(fact["quote"]),
            _plain(fact["file"]),
            _plain(fact_seen_label(fact)),
            _plain(fact_rank_label(fact)),
        ))
        for fact in facts
    )
    return "\n".join(lines) + "\n"


def render_html(report: dict) -> str:
    """Render a validated report as self-contained, script-free HTML."""
    validate_report_json(report)
    run = report["run"]
    read = report["read"]
    facts = report["facts"]
    contacts = read["contacts"]
    owner = "(not identified)" if read["likely_owner"] is None else read["likely_owner"]
    contact_text = ", ".join(contacts) if contacts else "(none)"

    exposures = [
        exposure for exposure in read["top_exposures"]
        if exposure["severity"] != "LOW"
    ]
    exposure_html = "".join(
        "<li><strong>"
        f"{_html(item['severity'])}</strong> {_html(item['text'])}</li>"
        for item in exposures
    )
    if not exposure_html:
        exposure_html = "<li>(none)</li>"
    facts_html = "".join(
        "<tr>"
        f"<td>{_html(fact['kind'])}</td>"
        f"<td><code>{_html(fact['quote'])}</code></td>"
        f"<td>{_html(fact['file'])}</td>"
        f"<td>{_html(fact_seen_label(fact))}</td>"
        f"<td>{_html(fact_rank_label(fact))}</td>"
        "</tr>"
        for fact in facts
    )

    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<meta http-equiv=\"Content-Security-Policy\" content=\"{HTML_CSP}\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>Analyst report — {_html(run['report_label'])}</title>"
        "<style>"
        "body{font:15px system-ui,sans-serif;margin:2rem;max-width:70rem;"
        "color:#18212b;background:#fff}h1,h2{color:#102a43}"
        ".risk{font-weight:700}.notice{font-weight:600;border-top:1px solid #bcccdc;"
        "padding-top:1rem}dl{display:grid;grid-template-columns:max-content 1fr;gap:.4rem 1rem}"
        "dt{font-weight:700}dd{margin:0}table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #bcccdc;padding:.45rem;text-align:left;vertical-align:top}"
        "th{background:#eef2f6}code{overflow-wrap:anywhere}"
        "</style></head><body><main>"
        "<section><h1>WHAT THIS IS</h1>"
        f"<p class=\"risk\">Risk: ● {_html(read['risk_level'])}</p>"
        f"<p>{_html(read['host_summary'])}</p><dl>"
        f"<dt>Likely owner</dt><dd>{_html(owner)}</dd>"
        f"<dt>Contacts</dt><dd>{_html(contact_text)}</dd>"
        f"<dt>Files read</dt><dd>{_html(run['files_read'])}</dd>"
        f"<dt>Flagged files</dt><dd>{_html(run['flagged_files'])}</dd>"
        "</dl><h2>TOP EXPOSURES</h2>"
        f"<ol>{exposure_html}</ol>"
        f"<p class=\"notice\">{_html(UNVERIFIED_NOTICE)}</p></section>"
        f"<section><h2>AFFILIATIONS</h2>{_affiliation_html(report)}</section>"
        "<section><h2>FACTS</h2><table><thead><tr>"
        "<th>Kind</th><th>Value</th><th>File</th><th>Seen</th><th>Rank</th>"
        f"</tr></thead><tbody>{facts_html}</tbody></table></section>"
        "</main></body></html>\n"
    )


def render_facts_csv(report: dict) -> str:
    """Render a validated report's grounded facts as spreadsheet-safe CSV."""
    validate_report_json(report)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(("Kind", "Value", "File", "Seen", "Rank"))
    for fact in report["facts"]:
        writer.writerow(tuple(csv_safe(value) for value in (
            fact["kind"], fact["quote"], fact["file"],
            fact_seen_label(fact), fact_rank_label(fact),
        )))
    return buffer.getvalue()


def render(report: dict, fmt: str) -> str:
    """Dispatch one validated report to a supported on-demand format."""
    validate_report_json(report)
    renderers: dict[str, Callable[[dict], str]] = {
        "md": render_markdown,
        "txt": render_text,
        "html": render_html,
        "csv": render_facts_csv,
    }
    if type(fmt) is not str or fmt not in renderers:
        raise ValueError("unsupported Analyst report format")
    return renderers[fmt](report)


def _plain(value: object) -> str:
    text = str(value)
    pieces: list[str] = []
    for char in text:
        codepoint = ord(char)
        if char == "\n":
            pieces.append("\\n")
        elif char == "\r":
            pieces.append("\\r")
        elif char == "\t":
            pieces.append("\\t")
        elif codepoint < 32 or codepoint == 127:
            pieces.append(f"\\x{codepoint:02x}")
        else:
            pieces.append(char)
    return "".join(pieces)


def _plain_optional(value: object) -> str:
    return "(not identified)" if value is None else _plain(value)


def _plain_contacts(values: list) -> str:
    return ", ".join(_plain(value) for value in values) if values else "(none)"


def _markdown(value: object) -> str:
    text = _plain(value)
    pieces: list[str] = []
    for index, char in enumerate(text):
        if char in "\\`*[]":
            pieces.append(f"\\{char}")
        elif char == "_":
            previous = text[index - 1] if index else ""
            following = text[index + 1] if index + 1 < len(text) else ""
            pieces.append(char if previous.isalnum() and following.isalnum() else "\\_")
        else:
            pieces.append(char)
    escaped = "".join(pieces)
    escaped = re.sub(r"^([ ]{0,3})([#>+\-])", r"\1\\\2", escaped)
    return re.sub(r"^([ ]{0,3}\d+)\.", r"\1\\.", escaped)


def _markdown_optional(value: object) -> str:
    return "(not identified)" if value is None else _markdown(value)


def _markdown_contacts(values: list) -> str:
    return ", ".join(_markdown(value) for value in values) if values else "(none)"


def _markdown_table(value: object) -> str:
    text = re.sub(r"[\r\n]+", " ", str(value))
    return _markdown(text).replace("|", "\\|")


def _markdown_code(value: object) -> str:
    text = re.sub(r"[\r\n]+", " ", str(value))
    text = _plain(text).replace("|", "\\|")
    longest = max((len(match.group()) for match in _BACKTICK_RUN.finditer(text)), default=0)
    delimiter = "`" * (longest + 1)
    return f"{delimiter} {text} {delimiter}"


def _html(value: object) -> str:
    return html.escape(str(value), quote=True)


__all__ = [
    "render",
    "render_facts_csv",
    "render_html",
    "render_markdown",
    "render_text",
]


def _affiliations(report: dict) -> dict:
    """Return the affiliations block, or an empty one for a pre-v4 report."""
    block = report.get("affiliations")
    return block if type(block) is dict else {}


_NO_AFFILIATIONS = "(no organization appears in enough files to be a pattern)"


def _toll_free_lines(report: dict, escape) -> list[str]:
    numbers = _affiliations(report).get("toll_free") or []
    if not numbers:
        return []
    total = _affiliations(report).get("toll_free_total", len(numbers))
    return [
        "",
        f"Toll-free numbers collected, not analysed "
        f"({len(numbers)} of {total} shown):",
        *(
            f"  {escape(item['value'])} - {item['files']} file(s), "
            f"e.g. {escape(item['example_file'])}"
            for item in numbers
        ),
    ]


def _affiliation_markdown(report: dict) -> list[str]:
    organizations = _affiliations(report).get("organizations") or []
    if not organizations:
        return [_NO_AFFILIATIONS, *_toll_free_lines(report, _markdown_table)]
    return [
        "| Organization | Files | Mentions |",
        "|---|---|---|",
        *(
            f"| {_markdown_table(item['domain'])} | {item['files']} "
            f"| {item['occurrences']} |"
            for item in organizations
        ),
        *_toll_free_lines(report, _markdown_table),
    ]


def _affiliation_text(report: dict) -> list[str]:
    organizations = _affiliations(report).get("organizations") or []
    if not organizations:
        return [_NO_AFFILIATIONS, *_toll_free_lines(report, _plain)]
    return [
        "Organization | Files | Mentions",
        *(
            f"{_plain(item['domain'])} | {item['files']} | {item['occurrences']}"
            for item in organizations
        ),
        *_toll_free_lines(report, _plain),
    ]


def _affiliation_html(report: dict) -> str:
    block = _affiliations(report)
    organizations = block.get("organizations") or []
    if organizations:
        rows = "".join(
            "<tr>"
            f"<td>{_html(item['domain'])}</td>"
            f"<td>{_html(item['files'])}</td>"
            f"<td>{_html(item['occurrences'])}</td>"
            "</tr>"
            for item in organizations
        )
        body = (
            "<table><thead><tr><th>Organization</th><th>Files</th>"
            f"<th>Mentions</th></tr></thead><tbody>{rows}</tbody></table>"
        )
    else:
        body = f"<p>{_html(_NO_AFFILIATIONS)}</p>"
    numbers = block.get("toll_free") or []
    if numbers:
        total = block.get("toll_free_total", len(numbers))
        items = "".join(
            f"<li><code>{_html(item['value'])}</code> &mdash; "
            f"{_html(item['files'])} file(s), e.g. {_html(item['example_file'])}"
            "</li>"
            for item in numbers
        )
        body += (
            "<p>Toll-free numbers collected, not analysed "
            f"({_html(len(numbers))} of {_html(total)} shown):</p><ul>{items}</ul>"
        )
    return body
