"""Pure data contracts and canonical builder for read-first ``report.json``."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable, Literal


REPORT_SCHEMA_VERSION = 1
UNVERIFIED_NOTICE = "Model's read - not verified. Facts below are grounded."
MAX_HOST_SUMMARY_CHARS = 1200

FactRank = Literal["HIGH", "MED", "low"]
RiskLevel = Literal["HIGH", "MED", "LOW"]

_FACT_RANKS = frozenset({"HIGH", "MED", "low"})
_RISK_LEVELS = frozenset({"HIGH", "MED", "LOW"})
_CATEGORIES = frozenset({"pii", "financial", "contact", "demographic"})
_FACT_SOURCES = frozenset({"detector", "model"})
_READ_MODES = frozenset({"quick", "full"})
_HIGH_KINDS = frozenset({
    "ssn", "passport", "card", "routing", "iban", "bank_account",
})


class ReportValidationError(ValueError):
    """A report value does not match the frozen versioned shape."""


class ReportVersionError(ReportValidationError):
    """The report version is missing or unsupported."""


@dataclass(frozen=True, slots=True)
class GroundedFact:
    kind: str
    category: str
    quote: str = field(repr=False)
    file: str = field(repr=False)
    provenance: str = field(repr=False)
    rank: FactRank
    source: str

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not str
            or type(self.category) is not str
            or type(self.quote) is not str
            or type(self.file) is not str
            or type(self.provenance) is not str
            or type(self.rank) is not str
            or type(self.source) is not str
            or not self.kind
            or not self.quote
            or self.category not in _CATEGORIES
            or self.rank not in _FACT_RANKS
            or self.source not in _FACT_SOURCES
        ):
            raise ReportValidationError("grounded fact is invalid")


@dataclass(frozen=True, slots=True)
class TopExposure:
    rank: int
    severity: RiskLevel
    text: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.rank) is not int
            or self.rank < 1
            or type(self.severity) is not str
            or self.severity not in _RISK_LEVELS
            or type(self.text) is not str
            or not self.text
        ):
            raise ReportValidationError("top exposure is invalid")


@dataclass(frozen=True, slots=True)
class HostRead:
    host_summary: str = field(repr=False)
    likely_owner: str | None = field(repr=False)
    contacts: tuple[str, ...] = field(repr=False)
    risk_level: RiskLevel
    top_exposures: tuple[TopExposure, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.host_summary) is not str
            or not 1 <= len(self.host_summary) <= MAX_HOST_SUMMARY_CHARS
            or (
                self.likely_owner is not None
                and type(self.likely_owner) is not str
            )
            or type(self.contacts) is not tuple
            or len(self.contacts) > 10
            or any(type(value) is not str for value in self.contacts)
            or type(self.risk_level) is not str
            or self.risk_level not in _RISK_LEVELS
            or type(self.top_exposures) is not tuple
            or len(self.top_exposures) > 5
            or any(type(value) is not TopExposure for value in self.top_exposures)
        ):
            raise ReportValidationError("host read is invalid")


@dataclass(frozen=True, slots=True)
class RunMeta:
    run_id: str
    report_label: str = field(repr=False)
    read_mode: str
    model_tag: str
    model_digest: str
    created_at_utc: str
    files_read: int
    files_total: int
    flagged_files: int

    def __post_init__(self) -> None:
        text_values = (
            self.run_id,
            self.report_label,
            self.model_tag,
            self.model_digest,
            self.created_at_utc,
        )
        counts = (self.files_read, self.files_total, self.flagged_files)
        if (
            any(type(value) is not str or not value for value in text_values)
            or type(self.read_mode) is not str
            or self.read_mode not in _READ_MODES
            or len(self.model_digest) != 64
            or any(char not in "0123456789abcdef" for char in self.model_digest)
            or any(type(value) is not int or value < 0 for value in counts)
            or self.files_read > self.files_total
        ):
            raise ReportValidationError("run metadata is invalid")


@dataclass(frozen=True, slots=True)
class Coverage:
    discovered: int
    terminal: int
    no_text_layer: int
    parse_failed: int
    unsupported: int

    def __post_init__(self) -> None:
        values = (
            self.discovered,
            self.terminal,
            self.no_text_layer,
            self.parse_failed,
            self.unsupported,
        )
        if (
            any(type(value) is not int or value < 0 for value in values)
            or self.terminal > self.discovered
        ):
            raise ReportValidationError("coverage is invalid")


def rank_fact(kind: str, category: str, source: str) -> FactRank:
    """Return the frozen deterministic rank for one grounded fact."""
    if kind in _HIGH_KINDS:
        return "HIGH"
    if kind == "dob" or (source == "model" and category in {"financial", "pii"}):
        return "MED"
    return "low"


def min_risk_from_facts(facts: Iterable[GroundedFact]) -> RiskLevel:
    """Return the minimum defensible host risk from grounded fact ranks."""
    minimum: RiskLevel = "LOW"
    for fact in facts:
        if type(fact) is not GroundedFact:
            raise TypeError("facts must contain GroundedFact values")
        if fact.rank == "HIGH":
            minimum = "HIGH"
        elif fact.rank == "MED" and minimum == "LOW":
            minimum = "MED"
    return minimum


def build_fallback_read(
    facts: Iterable[GroundedFact],
    *,
    files_read: int,
    files_total: int,
    flagged_files: int,
) -> HostRead:
    """Build a deterministic grounded read when the model read is unavailable."""
    fact_values = tuple(facts)
    if any(type(fact) is not GroundedFact for fact in fact_values):
        raise TypeError("facts must contain GroundedFact values")
    counts = (files_read, files_total, flagged_files)
    if (
        any(type(value) is not int or value < 0 for value in counts)
        or files_read > files_total
        or flagged_files > files_total
    ):
        raise ReportValidationError("fallback read counts are invalid")
    rank_order = {"HIGH": 0, "MED": 1, "low": 2}
    ordered = sorted(fact_values, key=lambda fact: rank_order[fact.rank])
    exposure_facts = tuple(
        fact for fact in ordered if fact.rank != "low"
    )[:5]
    return HostRead(
        host_summary=(
            f"Automated read unavailable. {files_read} files reviewed, "
            f"{flagged_files} flagged."
        ),
        likely_owner=None,
        contacts=(),
        risk_level=min_risk_from_facts(fact_values),
        top_exposures=tuple(
            TopExposure(
                rank=index,
                severity=fact.rank,
                text=f"{_fact_label(fact)} in {fact.file}",
            )
            for index, fact in enumerate(exposure_facts, start=1)
        ),
    )


def reconcile_risk(
    model_risk: str, facts: Iterable[GroundedFact]
) -> RiskLevel:
    """Never allow model judgment below the grounded minimum risk."""
    if type(model_risk) is not str or model_risk not in _RISK_LEVELS:
        raise ReportValidationError("model risk is invalid")
    grounded = min_risk_from_facts(facts)
    weights = {"LOW": 0, "MED": 1, "HIGH": 2}
    return grounded if weights[grounded] > weights[model_risk] else model_risk


def build_report_json(
    run: RunMeta,
    read: HostRead,
    facts: Iterable[GroundedFact],
    coverage: Coverage,
) -> dict[str, object]:
    """Build the exact version-1 report shape without performing I/O."""
    if type(run) is not RunMeta or type(read) is not HostRead:
        raise TypeError("run and read must use the report data contracts")
    if type(coverage) is not Coverage:
        raise TypeError("coverage must use the report data contract")

    fact_values = tuple(facts)
    if any(type(fact) is not GroundedFact for fact in fact_values):
        raise TypeError("facts must contain GroundedFact values")
    rank_order = {"HIGH": 0, "MED": 1, "low": 2}
    ordered_facts = tuple(sorted(
        fact_values,
        key=lambda fact: rank_order[fact.rank],
    ))
    # READ exposures are model judgments and may be semantic rather than regex
    # findings.  Preserve all validated items; grounded facts separately impose the
    # minimum risk level below.
    exposures = read.top_exposures
    reconciled_risk = reconcile_risk(read.risk_level, ordered_facts)

    report: dict[str, object] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "run": {
            "run_id": run.run_id,
            "report_label": run.report_label,
            "read_mode": run.read_mode,
            "model_tag": run.model_tag,
            "model_digest": run.model_digest,
            "created_at_utc": run.created_at_utc,
            "files_read": run.files_read,
            "files_total": run.files_total,
            "flagged_files": run.flagged_files,
        },
        "read": {
            "unverified_notice": UNVERIFIED_NOTICE,
            "host_summary": read.host_summary,
            "likely_owner": read.likely_owner,
            "contacts": list(read.contacts),
            "risk_level": reconciled_risk,
            "top_exposures": [
                {
                    "rank": index,
                    "severity": exposure.severity,
                    "text": exposure.text,
                }
                for index, exposure in enumerate(exposures, start=1)
            ],
        },
        "facts": [
            {
                "kind": fact.kind,
                "category": fact.category,
                "quote": fact.quote,
                "file": fact.file,
                "provenance": fact.provenance,
                "rank": fact.rank,
                "source": fact.source,
            }
            for fact in ordered_facts
        ],
        "coverage": {
            "discovered": coverage.discovered,
            "terminal": coverage.terminal,
            "no_text_layer": coverage.no_text_layer,
            "parse_failed": coverage.parse_failed,
            "unsupported": coverage.unsupported,
        },
    }
    validate_report_json(report)
    return report


def dumps_report(obj: dict[str, object]) -> str:
    """Return canonical sorted-key JSON suitable for UTF-8 encoding."""
    validate_report_json(obj)
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def validate_report_json(obj: dict[str, object]) -> None:
    """Fail closed unless *obj* exactly matches report schema version 1."""
    if type(obj) is not dict:
        raise ReportValidationError("report must be an object")
    if "report_schema_version" not in obj:
        raise ReportVersionError("report_schema_version is required")
    if type(obj["report_schema_version"]) is not int:
        raise ReportVersionError("report_schema_version must be an integer")
    if obj["report_schema_version"] != REPORT_SCHEMA_VERSION:
        raise ReportVersionError("report_schema_version is unsupported")

    _require_keys(
        obj,
        {"report_schema_version", "run", "read", "facts", "coverage"},
        "report",
    )
    run = _object(obj["run"], "run")
    _require_keys(run, {
        "run_id", "report_label", "read_mode", "model_tag", "model_digest",
        "created_at_utc", "files_read", "files_total", "flagged_files",
    }, "run")
    RunMeta(**run)

    read = _object(obj["read"], "read")
    _require_keys(read, {
        "unverified_notice", "host_summary", "likely_owner", "contacts",
        "risk_level", "top_exposures",
    }, "read")
    if read["unverified_notice"] != UNVERIFIED_NOTICE:
        raise ReportValidationError("read unverified notice is invalid")
    contacts = _list(read["contacts"], "read contacts")
    exposure_values = _list(read["top_exposures"], "top exposures")
    exposures: list[TopExposure] = []
    for expected_rank, value in enumerate(exposure_values, start=1):
        exposure = _object(value, "top exposure")
        _require_keys(exposure, {"rank", "severity", "text"}, "top exposure")
        parsed = TopExposure(**exposure)
        if parsed.rank != expected_rank:
            raise ReportValidationError("top exposures are not canonical")
        exposures.append(parsed)
    HostRead(
        host_summary=read["host_summary"],
        likely_owner=read["likely_owner"],
        contacts=tuple(contacts),
        risk_level=read["risk_level"],
        top_exposures=tuple(exposures),
    )

    fact_values = _list(obj["facts"], "facts")
    facts: list[GroundedFact] = []
    for value in fact_values:
        fact = _object(value, "fact")
        _require_keys(
            fact,
            {"kind", "category", "quote", "file", "provenance", "rank", "source"},
            "fact",
        )
        facts.append(GroundedFact(**fact))
    if [fact.rank for fact in facts] != sorted(
        (fact.rank for fact in facts),
        key={"HIGH": 0, "MED": 1, "low": 2}.__getitem__,
    ):
        raise ReportValidationError("facts are not sorted by rank")
    if read["risk_level"] != reconcile_risk(read["risk_level"], facts):
        raise ReportValidationError("read risk is below grounded facts")

    coverage = _object(obj["coverage"], "coverage")
    _require_keys(
        coverage,
        {"discovered", "terminal", "no_text_layer", "parse_failed", "unsupported"},
        "coverage",
    )
    Coverage(**coverage)


def _fact_label(fact: GroundedFact) -> str:
    labels = {
        "ssn": "SSN",
        "dob": "date of birth",
        "iban": "IBAN",
        "pii": "PII",
    }
    value = fact.category if fact.kind == "model" else fact.kind
    return labels.get(value, value.replace("_", " "))


def _object(value: object, name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise ReportValidationError(f"{name} must be an object")
    return value


def _list(value: object, name: str) -> list[object]:
    if type(value) is not list:
        raise ReportValidationError(f"{name} must be an array")
    return value


def _require_keys(
    value: dict[str, object], required: set[str], name: str
) -> None:
    if set(value) != required:
        raise ReportValidationError(f"{name} fields do not match the schema")
