"""Pure deterministic detectors for Analyst's all-document coverage track."""

from __future__ import annotations

import re
from datetime import date

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


def scan(text: str) -> list[DetectorHit]:
    """Return checksum/structure-validated hits in stable source order."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    hits: list[DetectorHit] = []

    _append_matches(hits, "ssn", _SSN, text, ssn_plausible)
    for match in _PAN.finditer(text):
        digits = re.sub(r"[ -]", "", match.group())
        if luhn_ok(digits):
            hits.append(_hit("card", match.group(), match.start(), match.end()))
    _append_matches(hits, "routing", _ABA, text, aba_ok)
    _append_matches(hits, "iban", _IBAN, text, iban_ok)
    _append_matches(hits, "email", _EMAIL, text)
    _append_matches(hits, "phone", _PHONE, text)
    for match in _DOB.finditer(text):
        if _valid_date(match.group()):
            hits.append(_hit("dob", match.group(), match.start(), match.end()))
    _append_group_matches(hits, "bank_account", _BANK_ACCOUNT, text)
    _append_group_matches(hits, "passport", _PASSPORT, text)

    lowered = text.lower()
    demographic_spans: list[tuple[int, int]] = []
    for term in sorted(_DEMOGRAPHIC_TERMS, key=len, reverse=True):
        start = lowered.find(term)
        while start >= 0:
            end = start + len(term)
            if not any(start < prior_end and prior_start < end
                       for prior_start, prior_end in demographic_spans):
                hits.append(_hit(
                    "demographic_term", text[start:end], start, end,
                ))
                demographic_spans.append((start, end))
            start = lowered.find(term, start + 1)

    unique = {(item.kind, item.start, item.end, item.value): item for item in hits}
    return sorted(unique.values(), key=lambda item: (item.start, item.end, item.kind))


def categories(text: str) -> set[Category]:
    return {KIND_TO_CATEGORY[item.kind] for item in scan(text)}


def _append_matches(
    hits: list[DetectorHit],
    kind: str,
    pattern: re.Pattern[str],
    text: str,
    validator=None,
) -> None:
    for match in pattern.finditer(text):
        value = match.group()
        if validator is None or validator(value):
            hits.append(_hit(kind, value, match.start(), match.end()))


def _append_group_matches(
    hits: list[DetectorHit], kind: str, pattern: re.Pattern[str], text: str
) -> None:
    for match in pattern.finditer(text):
        value = match.group("value")
        hits.append(_hit(kind, value, match.start("value"), match.end("value")))


def _valid_date(value: str) -> bool:
    month, day, year = (int(part) for part in value.split("/"))
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def _hit(kind: str, value: str, start: int, end: int) -> DetectorHit:
    return DetectorHit(kind=kind, value=value, start=start, end=end)
