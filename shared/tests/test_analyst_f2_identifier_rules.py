"""F2: published allocation rules decide what a detected value can be.

A regex plus a checksum says a string has the right shape. It does not say the
string is a real identifier. These rules are all public knowledge -- SSA
never-issued ranges, the NANP dialling plan, ISO/IEC 7812 issuer ranges,
Federal Reserve routing symbols, RFC 2606 -- and they sort a value into:

    impossible   cannot be real under any reading; never recorded
    suspect      almost certainly not real; recorded and ranked down
    valid        nothing known says otherwise

plus a separate question, whose it is: personal / organizational / unknown.
"""

from __future__ import annotations

import collections
import pathlib
from datetime import date

import pytest

from experimental.analyst.detectors import (
    DOB_LABELS,
    IdentifierScreen,
    ROUTING_LABELS,
    label_precedes,
    scan,
    screen_identifier,
)

TODAY = date(2026, 9, 25)
GOLD = pathlib.Path(__file__).resolve().parents[1] / "fixtures/analyst_gold/docs"


def _screen(kind, value, **kwargs) -> IdentifierScreen:
    return screen_identifier(kind, value, today=TODAY, **kwargs)


# --------------------------------------------------------------------------
# The two anchors. If either breaks, the rules have gone too far.
# --------------------------------------------------------------------------

def test_no_gold_set_value_is_ever_impossible():
    """The benchmark corpus is built from the values these rules screen.

    Every gold SSN is from a never-issued area, every gold phone uses the 555
    fiction exchange, and gold PANs are published test cards. They must all
    stay DETECTED -- demoted is fine, dropped would delete the benchmark's
    signal and break the per-document category floor in test_analyst_c1.
    """
    dropped = []
    for path in sorted(GOLD.glob("*.txt")):
        for hit in scan(path.read_text(encoding="utf-8")):
            screen = _screen(hit.kind, hit.value)
            if screen.impossible:
                dropped.append((path.name, hit.kind, hit.value, screen.reason))
    assert dropped == []


def test_the_canonical_test_card_is_suspect_and_not_impossible():
    """4242424242424242 is a 2-digit cycle AND a real Visa prefix.

    An earlier draft made repeating cycles impossible, and simulation against
    the gold set caught it dropping this number. It is a test card, not an
    impossible one -- which is the whole reason the middle tier exists.
    """
    screen = _screen("card", "4242424242424242")
    assert screen.plausibility == "suspect"
    assert not screen.impossible


# --------------------------------------------------------------------------
# SSN
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["000-00-0000", "999-99-9999", "555-55-5555"])
def test_a_repeated_digit_ssn_is_impossible(value):
    assert _screen("ssn", value).impossible


@pytest.mark.parametrize("value", ["078-05-1120", "219-09-9999", "123-45-6789"])
def test_a_published_placeholder_ssn_is_impossible(value):
    assert _screen("ssn", value).impossible


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("000-12-3456", "area 000"),
        ("666-12-3456", "area 666"),
        ("946-12-3456", "area 9xx"),
        ("123-00-4567", "group 00"),
        ("123-45-0000", "serial 0000"),
    ],
)
def test_a_never_issued_ssn_range_is_suspect_not_dropped(value, reason):
    screen = _screen("ssn", value)
    assert screen.plausibility == "suspect"
    assert screen.subject == "personal"


def test_a_nine_hundred_series_itin_is_named_as_an_itin():
    """9xx with group 70-88 is a real IRS number, just not an SSN."""
    assert _screen("ssn", "912-75-1234").reason == "an ITIN, not an SSN"


def test_an_ordinary_ssn_is_valid_and_personal():
    screen = _screen("ssn", "422-52-5082")
    assert screen.plausibility == "valid"
    assert screen.subject == "personal"


# --------------------------------------------------------------------------
# Card
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("8888888888888888", "every digit the same"),
        ("25925925925925924", "17 digits"),
        ("099378881987577720", "18 digits"),
        ("0000000000000000", "every digit the same"),
    ],
)
def test_a_structurally_impossible_card_is_dropped(value, why):
    assert _screen("card", value).impossible, why


def test_a_leading_zero_card_is_dropped():
    assert _screen("card", "0937888198757772").impossible


@pytest.mark.parametrize(
    "value",
    [
        "5454545454545454",   # 2-digit cycle
        "5925925925925926",   # 1/27 rendered as a float, last digit rounded
        "7037037037037037",   # 19/27
    ],
)
def test_a_repeating_decimal_rendered_as_a_float_is_suspect(value):
    """Spreadsheets hand these out. They pass Luhn and mean nothing."""
    assert _screen("card", value).plausibility == "suspect"


def test_a_card_outside_any_issued_range_is_suspect():
    assert _screen("card", "1139430284857571").plausibility == "suspect"


@pytest.mark.parametrize(
    "value",
    ["4111111111111111", "5105105105105100", "378282246310005", "30569309025904"],
)
def test_a_published_test_card_is_suspect(value):
    assert _screen("card", value).plausibility == "suspect"


def test_a_plausible_card_passes():
    assert _screen("card", "6401590457256461").plausibility == "valid"


# --------------------------------------------------------------------------
# Phone -- the NANP dialling plan
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value",
    ["123-456-7890", "012-345-6789", "411-555-1234", "210-911-1234", "210-100-1234"],
)
def test_an_undiallable_number_is_impossible(value):
    assert _screen("phone", value).impossible


@pytest.mark.parametrize("npa", ["800", "833", "844", "855", "866", "877", "888"])
def test_a_toll_free_number_is_real_but_organizational(npa):
    """The distinction that earned its own axis: real, just not personal."""
    screen = _screen("phone", f"{npa}-555-0123".replace("555", "234"))
    assert screen.plausibility == "valid", "a toll-free line is a real number"
    assert screen.subject == "organizational"


def test_a_premium_rate_number_is_organizational():
    assert _screen("phone", "900-234-5678").subject == "organizational"


def test_the_fiction_exchange_is_suspect():
    assert _screen("phone", "210-555-0142").plausibility == "suspect"


def test_an_ordinary_number_is_valid_and_unattributed():
    screen = _screen("phone", "210-234-5678")
    assert screen.plausibility == "valid"
    assert screen.subject == "unknown"


def test_a_leading_country_code_is_ignored():
    assert _screen("phone", "1-800-234-5678").subject == "organizational"


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value", ["a@example.com", "b@example.org", "c@host.invalid", "d@box.test"],
)
def test_a_reserved_documentation_domain_is_suspect_not_dropped(value):
    """The gold set is built from example.com, so this can never drop."""
    assert _screen("email", value).plausibility == "suspect"


@pytest.mark.parametrize(
    "value",
    ["info@mackie.com", "SALES@example2.net", "no-reply@bank.com",
     "customer.service@shop.com"],
)
def test_a_role_mailbox_is_real_but_organizational(value):
    screen = _screen("email", value)
    assert screen.plausibility == "valid"
    assert screen.subject == "organizational"


def test_a_personal_looking_address_is_left_alone():
    screen = _screen("email", "sabina.augustsson@hotmail.com")
    assert screen.plausibility == "valid"
    assert screen.subject == "unknown"


# --------------------------------------------------------------------------
# Routing -- label proximity carries the weight
# --------------------------------------------------------------------------

def test_an_unallocated_routing_prefix_is_suspect():
    assert _screen("routing", "597830437").plausibility == "suspect"


def test_an_allocated_prefix_with_a_label_is_valid():
    assert _screen("routing", "111001150", labeled=True).plausibility == "valid"


def test_an_allocated_prefix_with_no_label_is_suspect():
    """One nine-digit string in ten passes the ABA check by luck.

    A ZIP+4 written without its dash is the collision that matters.
    """
    assert _screen("routing", "111001150", labeled=False).plausibility == "suspect"


def test_an_unknown_label_state_is_never_held_against_a_value():
    assert _screen("routing", "111001150", labeled=None).plausibility == "valid"


# --------------------------------------------------------------------------
# Date of birth
# --------------------------------------------------------------------------

def test_a_future_date_cannot_be_a_birth_date():
    assert _screen("dob", "01/15/2030").impossible


def test_an_age_over_120_is_suspect():
    assert _screen("dob", "01/15/1900").plausibility == "suspect"


def test_an_unlabelled_date_is_suspect():
    """1,630 date hits in one corpus, and 1/19/2011 appeared 94 times."""
    assert _screen("dob", "01/19/2011", labeled=False).plausibility == "suspect"


def test_a_labelled_date_is_valid():
    assert _screen("dob", "01/19/2011", labeled=True).plausibility == "valid"


# --------------------------------------------------------------------------
# Label proximity itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "label",
    ["DOB:", "D.O.B.", "Date of Birth:", "date of bith", "Birthday", "born on",
     "BIRTH DATE", "birthdate"],
)
def test_the_birth_label_list_is_generous(label):
    text = f"{label} 01/19/2011"
    assert label_precedes(text, text.index("01/19"), DOB_LABELS)


@pytest.mark.parametrize("label", ["Routing", "RTN:", "ABA #", "transit number"])
def test_the_routing_label_list_covers_the_usual_spellings(label):
    text = f"{label} 111001150"
    assert label_precedes(text, text.index("111001150"), ROUTING_LABELS)


def test_a_distant_label_does_not_count():
    """A column header fifty rows up is not a label for this value."""
    text = "Date of Birth" + " " * 200 + "01/19/2011"
    assert not label_precedes(text, text.index("01/19"), DOB_LABELS)


# --------------------------------------------------------------------------
# IBAN, bank account, passport
# --------------------------------------------------------------------------

def test_an_iban_with_no_country_scheme_is_impossible():
    assert _screen("iban", "ZZ0980000890119243323053").impossible


def test_an_iban_of_the_wrong_length_for_its_country_is_impossible():
    assert _screen("iban", "SE09800008901192433230").impossible


def test_a_real_looking_swedish_iban_passes():
    assert _screen("iban", "SE0980000890119243323053").plausibility == "valid"


@pytest.mark.parametrize("value", ["Number", "number", "motors", "picture"])
def test_a_passport_value_with_no_digit_is_impossible(value):
    """130 hits in one corpus, four distinct values, every one a false one."""
    assert _screen("passport", value).impossible


def test_a_real_looking_passport_number_passes():
    assert _screen("passport", "X4820371").plausibility == "valid"


def test_the_c1_fixture_passport_is_suspect_because_it_counts_up():
    """X1234567 is the test fixture, and 1234567 really is a counted run.

    Suspect is the honest answer: still detected, ranked down. Dropping it
    would break the c1 detector fixture.
    """
    screen = _screen("passport", "X1234567")
    assert screen.plausibility == "suspect"
    assert not screen.impossible


def test_a_repeated_digit_bank_account_is_impossible():
    assert _screen("bank_account", "000000000000").impossible


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------

def test_an_unknown_kind_is_left_alone():
    assert _screen("something_else", "whatever").plausibility == "valid"


def test_a_non_string_is_refused():
    with pytest.raises(TypeError):
        screen_identifier("ssn", 12345)
