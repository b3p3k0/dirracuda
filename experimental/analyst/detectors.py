"""Pure deterministic detectors for Analyst's all-document coverage track."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Callable, Final

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


# ---------------------------------------------------------------------------
# Identifier screening (F2)
#
# A regex plus a checksum says a string has the right shape. It does not say
# the string is a real identifier. Published allocation rules do, and they are
# public: SSA never-issued ranges, the NANP dialling plan, ISO/IEC 7812 issuer
# ranges, Federal Reserve routing symbols, RFC 2606 reserved domains.
#
# Two independent questions, deliberately kept apart:
#
#   plausibility  is this a real identifier?      valid / suspect / impossible
#   subject       whose is it?                    personal / organizational /
#                                                 unknown
#
# A toll-free number is entirely real and belongs to a business. Calling it
# "suspect" would be false, which is why it does not share a field with the
# SSA never-issued ranges.
#
# Only IMPOSSIBLE is refused outright: a value that cannot be a real identifier
# under any reading. Everything else is recorded and ranked down, because
# coverage honesty is the product (frozen contract section 4) and a quietly
# deleted fact cannot be argued with.
# ---------------------------------------------------------------------------

VALID: Final = "valid"
SUSPECT: Final = "suspect"
IMPOSSIBLE: Final = "impossible"

PERSONAL: Final = "personal"
ORGANIZATIONAL: Final = "organizational"
UNKNOWN: Final = "unknown"

#: Labels that mark a date as a birth date. Deliberately generous: abbreviated
#: and spelled out, with and without punctuation, and common misspellings.
DOB_LABELS: Final = (
    "date of birth", "date of brith", "date of bith", "date ofbirth",
    "birth date", "birthdate", "birth day", "birthday", "born on", "born:",
    "d.o.b", "d o b", "dob", "date born", "birth",
)
#: Labels that mark nine digits as a bank routing number.
ROUTING_LABELS: Final = (
    "routing", "rtn", "aba", "transit", "ach", "bank number", "routing/transit",
)
#: How far before a value a label may sit and still be about it. One table
#: cell, one form field, or one label-colon-value pair -- not a column header
#: fifty rows up.
LABEL_WINDOW: Final = 48

_TOLL_FREE_NPA: Final = frozenset({"800", "833", "844", "855", "866", "877", "888"})
_PREMIUM_NPA: Final = frozenset({"900", "976"})
#: Reserved for carrier testing and expansion; never assigned to a subscriber.
_UNASSIGNED_NPA: Final = frozenset({"950", "958", "959"})

#: Published placeholder SSNs. 078-05-1120 is the Woolworth wallet specimen,
#: 219-09-9999 appeared in an SSA pamphlet, and 123-45-6789 is the universal
#: example. All three have been claimed by thousands of people in error.
_PLACEHOLDER_SSN: Final = frozenset({"078051120", "219099999", "123456789"})

#: Vendor-documented test card numbers. Real Luhn, real issuer prefixes, and
#: no real account behind any of them.
_TEST_PANS: Final = frozenset({
    "4242424242424242", "5555555555554444", "378282246310005",
    "6011111111111117", "4000000000000002", "4000000000009995",
    "4000000000000069", "4000000000000127", "4111111111111111",
    "4012888888881881", "5105105105105100", "371449635398431",
    "6011000990139424", "30569309025904", "3530111333300000",
})

#: Digit counts issuers actually use. 17 and 18 are the giveaway of a long
#: number that passed through a float: an IEEE double renders 1/27 as
#: 25925925925925924.
_CARD_LENGTHS: Final = frozenset({13, 14, 15, 16, 19})

#: Reserved by RFC 2606 and RFC 6761. Never routable, and the Analyst gold set
#: is built from example.com, so these are suspect and never impossible.
_RESERVED_EMAIL_DOMAINS: Final = frozenset({
    "example.com", "example.org", "example.net", "example.edu",
    "localhost", "invalid", "test", "local",
})
#: Mailboxes that exist to be published. Real addresses, not personal ones.
_ROLE_MAILBOXES: Final = frozenset({
    "info", "sales", "support", "admin", "administrator", "noreply",
    "no-reply", "donotreply", "do-not-reply", "postmaster", "abuse",
    "webmaster", "hostmaster", "help", "helpdesk", "contact", "enquiries",
    "inquiries", "office", "billing", "accounts", "careers", "jobs",
    "marketing", "newsletter", "feedback", "service", "customerservice",
})

#: Federal Reserve routing symbols. Everything outside these was never
#: allocated, and a nine-digit string has a one-in-ten chance of passing the
#: ABA checksum by luck, so this carries real weight.
_ABA_PREFIXES: Final = (
    (0, 12), (21, 32), (61, 72), (80, 80),
)

#: IBAN length by country, as published by SWIFT. A mod-97 pass at the wrong
#: length is a coincidence, not an account.
_IBAN_LENGTHS: Final = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16,
    "BG": 22, "BH": 22, "BR": 29, "BY": 28, "CH": 21, "CR": 22, "CY": 28,
    "CZ": 24, "DE": 22, "DK": 18, "DO": 28, "EE": 20, "EG": 29, "ES": 24,
    "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18,
    "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23,
    "IS": 26, "IT": 27, "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32,
    "LI": 21, "LT": 20, "LU": 20, "LV": 21, "LY": 25, "MC": 27, "MD": 24,
    "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15,
    "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22,
    "SA": 24, "SC": 31, "SD": 18, "SE": 24, "SI": 19, "SK": 24, "SM": 27,
    "ST": 25, "SV": 28, "TL": 23, "TN": 24, "TR": 26, "UA": 29, "VA": 22,
    "VG": 24, "XK": 20,
}
#: Published IBAN examples, the SWIFT registry's own specimens.
_TEST_IBANS: Final = frozenset({
    "DE89370400440532013000", "GB82WEST12345698765432",
    "FR1420041010050500013M02606", "GB29NWBK60161331926819",
})

_KINDS_WITH_DIGIT_RUNS: Final = frozenset({
    "card", "ssn", "routing", "bank_account", "passport",
})


@dataclass(frozen=True, slots=True)
class IdentifierScreen:
    """What published allocation rules say about one detected value."""

    plausibility: str
    subject: str = UNKNOWN
    reason: str = ""

    @property
    def impossible(self) -> bool:
        return self.plausibility == IMPOSSIBLE


_VALID_SCREEN: Final = IdentifierScreen(VALID)


def _digits(value: str) -> str:
    return "".join(char for char in value if char.isdigit())


def _all_one_digit(digits: str) -> bool:
    return bool(digits) and len(set(digits)) == 1


def _monotonic_run(digits: str) -> bool:
    """True for 123456789 and 987654321: a counted sequence, not an identifier."""
    if len(digits) < 6:
        return False
    steps = {int(b) - int(a) for a, b in zip(digits, digits[1:])}
    return steps in ({1}, {-1})


def _repeating_cycle(digits: str, max_period: int = 6) -> int | None:
    """Return the period of a short repeated pattern, or None.

    A repeating decimal rendered into a fixed-width field looks exactly like
    this. 1/27 becomes 592592592592592..., and the field's last digit carries
    the rounding, so the tail is checked separately by the caller.
    """
    for period in range(1, max_period + 1):
        if len(digits) < period * 2:
            break
        if all(digits[i] == digits[i % period] for i in range(len(digits))):
            return period
    return None


def _float_artifact(digits: str) -> str | None:
    period = _repeating_cycle(digits)
    if period is not None:
        return f"{period}-digit pattern repeated"
    period = _repeating_cycle(digits[:-1])
    if period is not None:
        return f"{period}-digit pattern repeated, last digit rounded"
    return None


def _card_issuer_known(digits: str) -> bool:
    """ISO/IEC 7812 issuer identification ranges actually in service."""
    return (
        digits[:1] == "4"
        or digits[:2] in {"34", "37", "36", "38", "39"}
        or "51" <= digits[:2] <= "55"
        or "2221" <= digits[:4] <= "2720"
        or digits[:1] == "6"
        or "300" <= digits[:3] <= "305"
    )


def _aba_prefix_allocated(digits: str) -> bool:
    prefix = int(digits[:2])
    return any(low <= prefix <= high for low, high in _ABA_PREFIXES)


def _nanp_digits(value: str) -> str:
    digits = _digits(value)
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    return digits


def label_precedes(text: str, start: int, labels: tuple[str, ...]) -> bool:
    """True when one of ``labels`` sits just before ``start`` in ``text``.

    The window is one form field wide on purpose. A column header fifty rows
    above a value is not a label for that value, and pretending otherwise would
    make the check meaningless.
    """
    window = text[max(0, start - LABEL_WINDOW):start].casefold()
    return any(label in window for label in labels)


def _screen_ssn(digits: str) -> IdentifierScreen:
    if _all_one_digit(digits) or _monotonic_run(digits):
        return IdentifierScreen(IMPOSSIBLE, PERSONAL, "a counted or repeated run")
    if digits in _PLACEHOLDER_SSN:
        return IdentifierScreen(IMPOSSIBLE, PERSONAL, "a published placeholder SSN")
    area, group, serial = digits[:3], digits[3:5], digits[5:]
    if area == "000":
        return IdentifierScreen(SUSPECT, PERSONAL, "area 000 was never issued")
    if area == "666":
        return IdentifierScreen(SUSPECT, PERSONAL, "area 666 was never issued")
    if area >= "900":
        # 9xx with group 70-88 is an ITIN: a real IRS taxpayer number, just not
        # an SSN. Saying so beats calling it "never issued".
        if "70" <= group <= "88":
            return IdentifierScreen(
                SUSPECT, PERSONAL, "an ITIN, not an SSN",
            )
        return IdentifierScreen(SUSPECT, PERSONAL, "area 9xx was never issued")
    if group == "00":
        return IdentifierScreen(SUSPECT, PERSONAL, "group 00 was never issued")
    if serial == "0000":
        return IdentifierScreen(SUSPECT, PERSONAL, "serial 0000 was never issued")
    return IdentifierScreen(VALID, PERSONAL)


def _screen_card(digits: str) -> IdentifierScreen:
    if _all_one_digit(digits):
        return IdentifierScreen(IMPOSSIBLE, UNKNOWN, "every digit the same")
    if len(digits) not in _CARD_LENGTHS:
        return IdentifierScreen(
            IMPOSSIBLE, UNKNOWN, f"no issuer uses {len(digits)} digits",
        )
    if digits[:1] == "0":
        return IdentifierScreen(IMPOSSIBLE, UNKNOWN, "no issuer number starts with 0")
    if digits in _TEST_PANS:
        return IdentifierScreen(SUSPECT, UNKNOWN, "a published test card number")
    artifact = _float_artifact(digits)
    if artifact is not None:
        return IdentifierScreen(SUSPECT, UNKNOWN, artifact)
    if _monotonic_run(digits):
        return IdentifierScreen(SUSPECT, UNKNOWN, "a counted run")
    if not _card_issuer_known(digits):
        return IdentifierScreen(
            SUSPECT, UNKNOWN, f"{digits[:2]} is not an issued card range",
        )
    return _VALID_SCREEN


def _screen_routing(digits: str, labeled: bool | None) -> IdentifierScreen:
    if _all_one_digit(digits):
        return IdentifierScreen(IMPOSSIBLE, UNKNOWN, "every digit the same")
    if not _aba_prefix_allocated(digits):
        return IdentifierScreen(
            SUSPECT, UNKNOWN, f"prefix {digits[:2]} was never allocated",
        )
    if labeled is False:
        # One nine-digit string in ten passes the ABA checksum, so an unlabelled
        # one is as likely to be a ZIP+4 written without its dash.
        return IdentifierScreen(
            SUSPECT, UNKNOWN, "nine digits with no routing label nearby",
        )
    return _VALID_SCREEN


def _screen_phone(value: str) -> IdentifierScreen:
    digits = _nanp_digits(value)
    if len(digits) != 10:
        return _VALID_SCREEN
    npa, nxx = digits[:3], digits[3:6]
    for part, name in ((npa, "area code"), (nxx, "exchange")):
        if part[0] in "01":
            return IdentifierScreen(
                IMPOSSIBLE, UNKNOWN, f"{name} {part} cannot start with 0 or 1",
            )
        if part[1:] == "11":
            return IdentifierScreen(
                IMPOSSIBLE, UNKNOWN, f"{name} {part} is a service code",
            )
    if npa in _TOLL_FREE_NPA:
        return IdentifierScreen(VALID, ORGANIZATIONAL, "a toll-free number")
    if npa in _PREMIUM_NPA:
        return IdentifierScreen(VALID, ORGANIZATIONAL, "a premium-rate number")
    if npa in _UNASSIGNED_NPA:
        return IdentifierScreen(
            SUSPECT, UNKNOWN, f"area code {npa} is not assigned to subscribers",
        )
    if nxx == "555":
        return IdentifierScreen(
            SUSPECT, UNKNOWN, "exchange 555 is reserved for fiction",
        )
    return _VALID_SCREEN


def _screen_email(value: str) -> IdentifierScreen:
    local, _, domain = value.rpartition("@")
    domain = domain.casefold()
    if domain in _RESERVED_EMAIL_DOMAINS or domain.rpartition(".")[2] in {
        "test", "invalid", "localhost", "example", "local",
    }:
        return IdentifierScreen(
            SUSPECT, UNKNOWN, "a reserved documentation domain",
        )
    if local.casefold().replace(".", "").replace("_", "") in _ROLE_MAILBOXES:
        return IdentifierScreen(VALID, ORGANIZATIONAL, "a role mailbox")
    return _VALID_SCREEN


def _screen_dob(value: str, labeled: bool | None, today: date) -> IdentifierScreen:
    month, day, year = (int(part) for part in value.split("/"))
    try:
        born = date(year, month, day)
    except ValueError:
        return IdentifierScreen(IMPOSSIBLE, PERSONAL, "not a real date")
    if born > today:
        return IdentifierScreen(IMPOSSIBLE, PERSONAL, "a date in the future")
    if year <= today.year - 120:
        return IdentifierScreen(SUSPECT, PERSONAL, "implies an age over 120")
    if (month, day) in {(1, 1)} and year in {1900, 1970}:
        return IdentifierScreen(SUSPECT, PERSONAL, "an epoch or null-date default")
    if labeled is False:
        # Every invoice, statement and print footer carries a date. Without a
        # label nearby there is nothing to say this one is a birth date.
        return IdentifierScreen(
            SUSPECT, PERSONAL, "a date with no birth label nearby",
        )
    return IdentifierScreen(VALID, PERSONAL)


def _screen_iban(value: str) -> IdentifierScreen:
    compact = value.replace(" ", "").upper()
    country = compact[:2]
    expected = _IBAN_LENGTHS.get(country)
    if expected is None:
        return IdentifierScreen(
            IMPOSSIBLE, UNKNOWN, f"{country} has no IBAN scheme",
        )
    if len(compact) != expected:
        return IdentifierScreen(
            IMPOSSIBLE, UNKNOWN,
            f"{country} IBANs are {expected} characters, not {len(compact)}",
        )
    if compact in _TEST_IBANS:
        return IdentifierScreen(SUSPECT, UNKNOWN, "a published example IBAN")
    return _VALID_SCREEN


def _screen_bank_account(digits: str) -> IdentifierScreen:
    if _all_one_digit(digits):
        return IdentifierScreen(IMPOSSIBLE, UNKNOWN, "every digit the same")
    if _monotonic_run(digits):
        return IdentifierScreen(SUSPECT, UNKNOWN, "a counted run")
    if not 4 <= len(digits) <= 17:
        return IdentifierScreen(
            SUSPECT, UNKNOWN, f"{len(digits)} digits is outside account length",
        )
    return _VALID_SCREEN


def _screen_passport(value: str, digits: str) -> IdentifierScreen:
    if not any(char.isdigit() for char in value):
        # The pattern matches a "passport" label then any six to nine
        # alphanumerics, so the word following the label was being reported as
        # a passport number.
        return IdentifierScreen(
            IMPOSSIBLE, PERSONAL, "no digit in the value",
        )
    if _all_one_digit(digits) or _monotonic_run(digits):
        return IdentifierScreen(SUSPECT, PERSONAL, "a counted or repeated run")
    return IdentifierScreen(VALID, PERSONAL)


def screen_identifier(
    kind: str,
    value: str,
    *,
    labeled: bool | None = None,
    today: date | None = None,
) -> IdentifierScreen:
    """Return what published allocation rules say about one detected value.

    ``labeled`` is whether a kind-appropriate label sits just before the value
    in its source text. Only ``dob`` and ``routing`` use it, and only the
    scanner can supply it, because the report layer holds the hit but not the
    document. ``None`` means unknown, which is never held against a value.
    """
    if type(kind) is not str or type(value) is not str:
        raise TypeError("kind and value must be strings")
    digits = _digits(value) if kind in _KINDS_WITH_DIGIT_RUNS else ""
    if kind == "ssn":
        return _screen_ssn(digits)
    if kind == "card":
        return _screen_card(digits)
    if kind == "routing":
        return _screen_routing(digits, labeled)
    if kind == "phone":
        return _screen_phone(value)
    if kind == "email":
        return _screen_email(value)
    if kind == "dob":
        return _screen_dob(value, labeled, today or date.today())
    if kind == "iban":
        return _screen_iban(value)
    if kind == "bank_account":
        return _screen_bank_account(digits)
    if kind == "passport":
        return _screen_passport(value, digits)
    if kind == "demographic_term":
        return IdentifierScreen(VALID, PERSONAL)
    return _VALID_SCREEN


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
