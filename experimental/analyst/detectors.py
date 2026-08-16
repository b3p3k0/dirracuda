"""Pure deterministic detectors for Analyst's all-document coverage track."""

from __future__ import annotations

import re
from datetime import date
from typing import Callable

from .models import Category, DetectorHit


def luhn_ok(digits: str) -> bool:
    if not digits.isascii() or not digits.isdigit() or len(digits) < 12:
        return False
    total = 0
    alternate = False
    for char in reversed(digits):
        value = int(char)
        if alternate:
            value *= 2
            if value > 9:
                value -= 9
        total += value
        alternate = not alternate
    return total % 10 == 0


def aba_ok(digits: str) -> bool:
    if not (digits.isascii() and digits.isdigit() and len(digits) == 9):
        return False
    values = [int(char) for char in digits]
    total = (
        3 * (values[0] + values[3] + values[6])
        + 7 * (values[1] + values[4] + values[7])
        + values[2]
        + values[5]
        + values[8]
    )
    return total % 10 == 0


def iban_ok(value: str) -> bool:
    compact = value.replace(" ", "").upper()
    if not (
        15 <= len(compact) <= 34
        and compact[:2].isascii()
        and compact[:2].isalpha()
        and compact[2:4].isascii()
        and compact[2:4].isdigit()
        and compact[4:].isascii()
        and compact[4:].isalnum()
    ):
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(char, 36)) for char in rearranged)
    return int(numeric) % 97 == 1


def ssn_plausible(value: str) -> bool:
    """Accept the shape; reserved ranges remain useful synthetic fixtures."""
    parts = value.split("-")
    return (
        tuple(map(len, parts)) == (3, 2, 4)
        and all(part.isascii() and part.isdigit() for part in parts)
    )


_PAN = re.compile(r"\b(?:\d[ -]?){12,18}\d\b", re.ASCII)
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b", re.ASCII)
_ABA = re.compile(r"\b\d{9}\b", re.ASCII)
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b", re.ASCII)
_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", re.ASCII
)
_PHONE = re.compile(
    r"(?<!\w)(?:\(\d{3}\)|\d{3})[ .-]\d{3}[ .-]\d{4}\b", re.ASCII
)
_DOB = re.compile(
    r"\b(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])/(?:19|20)\d{2}\b",
    re.ASCII,
)
_BANK_ACCOUNT = re.compile(
    r"\b(?:ACH\s+|bank\s+)?(?:account|acct)(?:\s+(?:number|no\.?))?\s*[:#-]?\s*"
    r"(?P<value>\d(?:[ -]?\d){5,16})\b",
    re.IGNORECASE | re.ASCII,
)
_PASSPORT = re.compile(
    r"\bpassport(?:\s+(?:number|no\.?))?\s*[:#-]?\s*"
    r"(?P<value>[A-Z0-9]{6,9})\b",
    re.IGNORECASE | re.ASCII,
)
_DEMOGRAPHIC_TERMS = (
    "hispanic or latino",
    "not hispanic or latino",
    "black or african american",
    "american indian or alaska native",
    "two or more races",
    "declined to state",
    "non-binary",
    "widowed",
    "divorced",
)

KIND_TO_CATEGORY = {
    "ssn": Category.PII,
    "dob": Category.PII,
    "passport": Category.PII,
    "card": Category.FINANCIAL,
    "routing": Category.FINANCIAL,
    "bank_account": Category.FINANCIAL,
    "iban": Category.FINANCIAL,
    "email": Category.CONTACT,
    "phone": Category.CONTACT,
    "demographic_term": Category.DEMOGRAPHIC,
}


class _DetectorLimitReached(Exception):
    pass


class DetectorScanCancelled(Exception):
    """Raised without partial evidence when cooperative scanning is cancelled."""


def scan(text: str) -> list[DetectorHit]:
    """Return checksum/structure-validated hits in stable source order."""
    hits, overflow = _scan(text, max_hits=None)
    assert not overflow
    return hits


def scan_bounded(
    text: str,
    *,
    max_hits: int,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[list[DetectorHit], bool]:
    """Return no partial evidence when unique findings exceed ``max_hits``."""
    if type(max_hits) is not int or max_hits <= 0:
        raise ValueError("max_hits must be a positive integer")
    if cancel_check is not None and not callable(cancel_check):
        raise TypeError("cancel_check must be callable")
    return _scan(text, max_hits=max_hits, cancel_check=cancel_check)


def _scan(
    text: str,
    *,
    max_hits: int | None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[list[DetectorHit], bool]:
    if type(text) is not str:
        raise TypeError("text must be a string")
    unique: dict[tuple[str, int, int, str], DetectorHit] = {}

    def check_cancelled() -> None:
        if cancel_check is not None and cancel_check():
            raise DetectorScanCancelled

    def record(hit: DetectorHit) -> None:
        check_cancelled()
        key = (hit.kind, hit.start, hit.end, hit.value)
        if key in unique:
            return
        unique[key] = hit
        if max_hits is not None and len(unique) > max_hits:
            raise _DetectorLimitReached

    try:
        check_cancelled()
        _append_matches(record, "ssn", _SSN, text, ssn_plausible)
        check_cancelled()
        for match in _PAN.finditer(text):
            digits = re.sub(r"[ -]", "", match.group())
            if luhn_ok(digits):
                record(_hit("card", match.group(), match.start(), match.end()))
        _append_matches(record, "routing", _ABA, text, aba_ok)
        check_cancelled()
        _append_matches(record, "iban", _IBAN, text, iban_ok)
        check_cancelled()
        _append_matches(record, "email", _EMAIL, text)
        check_cancelled()
        _append_matches(record, "phone", _PHONE, text)
        check_cancelled()
        for match in _DOB.finditer(text):
            if _valid_date(match.group()):
                record(_hit("dob", match.group(), match.start(), match.end()))
        _append_group_matches(record, "bank_account", _BANK_ACCOUNT, text)
        check_cancelled()
        _append_group_matches(record, "passport", _PASSPORT, text)
        check_cancelled()

        lowered = text.lower()
        demographic_spans: list[tuple[int, int]] = []
        for term in sorted(_DEMOGRAPHIC_TERMS, key=len, reverse=True):
            check_cancelled()
            start = lowered.find(term)
            while start >= 0:
                check_cancelled()
                end = start + len(term)
                if not any(start < prior_end and prior_start < end
                           for prior_start, prior_end in demographic_spans):
                    record(_hit(
                        "demographic_term", text[start:end], start, end,
                    ))
                    demographic_spans.append((start, end))
                start = lowered.find(term, start + 1)
    except _DetectorLimitReached:
        return [], True

    check_cancelled()
    return sorted(
        unique.values(), key=lambda item: (item.start, item.end, item.kind)
    ), False


def categories(text: str) -> set[Category]:
    return {KIND_TO_CATEGORY[item.kind] for item in scan(text)}


def _append_matches(
    record: Callable[[DetectorHit], None],
    kind: str,
    pattern: re.Pattern[str],
    text: str,
    validator=None,
) -> None:
    for match in pattern.finditer(text):
        value = match.group()
        if validator is None or validator(value):
            record(_hit(kind, value, match.start(), match.end()))


def _append_group_matches(
    record: Callable[[DetectorHit], None],
    kind: str,
    pattern: re.Pattern[str],
    text: str,
) -> None:
    for match in pattern.finditer(text):
        value = match.group("value")
        record(_hit(kind, value, match.start("value"), match.end("value")))


def _valid_date(value: str) -> bool:
    month, day, year = (int(part) for part in value.split("/"))
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def _hit(kind: str, value: str, start: int, end: int) -> DetectorHit:
    return DetectorHit(kind=kind, value=value, start=start, end=end)
