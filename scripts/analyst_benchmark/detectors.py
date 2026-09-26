"""
Reference deterministic detectors for the benchmark's coverage track.

DISPOSITION: ported to production in C1 as a subset of the full detector set.

Pure: no I/O, no imports beyond the standard library. These own identifier
counts; the model never does (CONTRACT.md §7, RESEARCH_NOTES "two tracks").
Checksum-validated where a checksum exists, so a lookalike cannot pass.
"""
from __future__ import annotations

import re
from typing import Dict, List, NamedTuple


class Hit(NamedTuple):
    kind: str
    value: str
    start: int
    end: int


# --------------------------------------------------------------------------
# Checksums
# --------------------------------------------------------------------------
def luhn_ok(digits: str) -> bool:
    if not digits.isdigit() or len(digits) < 12:
        return False
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def aba_ok(digits: str) -> bool:
    """US ABA routing checksum: 3(d1+d4+d7)+7(d2+d5+d8)+(d3+d6+d9) = 0 mod 10."""
    if not (digits.isdigit() and len(digits) == 9):
        return False
    d = [int(c) for c in digits]
    total = (3 * (d[0] + d[3] + d[6])
             + 7 * (d[1] + d[4] + d[7])
             + (d[2] + d[5] + d[8]))
    return total % 10 == 0


def iban_ok(value: str) -> bool:
    """ISO 13616 mod-97 check."""
    s = value.replace(" ", "").upper()
    if not (15 <= len(s) <= 34) or not s[:2].isalpha() or not s[2:4].isdigit():
        return False
    rearranged = s[4:] + s[:4]
    try:
        numeric = "".join(str(int(c, 36)) for c in rearranged)
    except ValueError:
        return False
    return int(numeric) % 97 == 1


def ssn_plausible(value: str) -> bool:
    """Structural SSN check. Area 000/666/900-999 are never issued, but the gold
    set uses those ranges deliberately, so structure alone decides here and the
    caller keeps the never-issued property as fixture provenance, not a filter."""
    parts = value.split("-")
    if len(parts) != 3:
        return False
    a, g, s = parts
    return (len(a), len(g), len(s)) == (3, 2, 4) and all(p.isdigit() for p in parts)


# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------
_PAN = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_ABA = re.compile(r"\b\d{9}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"\(?\b\d{3}\)?[ .-]\d{3}-\d{4}\b")
_DOB = re.compile(r"\b(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])/(?:19|20)\d{2}\b")

_DEMOGRAPHIC_TERMS = (
    "hispanic or latino", "not hispanic or latino", "black or african american",
    "american indian or alaska native", "two or more races", "declined to state",
    "non-binary", "widowed", "divorced",
)


def scan(text: str) -> List[Hit]:
    """All checksum-validated identifier hits, ordered by position."""
    hits: List[Hit] = []

    for m in _SSN.finditer(text):
        if ssn_plausible(m.group()):
            hits.append(Hit("ssn", m.group(), m.start(), m.end()))

    for m in _PAN.finditer(text):
        digits = re.sub(r"[ -]", "", m.group())
        if luhn_ok(digits):
            hits.append(Hit("card", m.group(), m.start(), m.end()))

    for m in _ABA.finditer(text):
        if aba_ok(m.group()):
            hits.append(Hit("routing", m.group(), m.start(), m.end()))

    for m in _IBAN.finditer(text):
        if iban_ok(m.group()):
            hits.append(Hit("iban", m.group(), m.start(), m.end()))

    for m in _EMAIL.finditer(text):
        hits.append(Hit("email", m.group(), m.start(), m.end()))

    for m in _PHONE.finditer(text):
        hits.append(Hit("phone", m.group(), m.start(), m.end()))

    for m in _DOB.finditer(text):
        hits.append(Hit("dob", m.group(), m.start(), m.end()))

    low = text.lower()
    for term in _DEMOGRAPHIC_TERMS:
        start = low.find(term)
        while start != -1:
            hits.append(Hit("demographic_term", text[start:start + len(term)],
                            start, start + len(term)))
            start = low.find(term, start + 1)

    return sorted(hits, key=lambda h: (h.start, h.kind))


KIND_TO_CATEGORY: Dict[str, str] = {
    "ssn": "pii",
    "dob": "pii",
    "card": "financial",
    "routing": "financial",
    "iban": "financial",
    "email": "contact",
    "phone": "contact",
    "demographic_term": "demographic",
}


def categories(text: str) -> set[str]:
    """Categories the deterministic track can evidence in this text.

    This is the detector-scanned view. It is NOT model-reviewed coverage and is
    never reported as such (CONTRACT.md §4).
    """
    return {KIND_TO_CATEGORY[h.kind] for h in scan(text) if h.kind in KIND_TO_CATEGORY}
