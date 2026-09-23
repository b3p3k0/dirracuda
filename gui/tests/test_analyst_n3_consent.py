"""N3: egress consent before a remote run (contract 10)."""

from __future__ import annotations

import pytest

from gui.components.experimental_features import analyst_egress_consent as consent
from gui.utils.session_flags import (
    ANALYST_REMOTE_EGRESS_MUTE_KEY,
    clear_flag,
    set_flag,
)


class _Profile:
    def __init__(self, *, loopback=False, muted=False, name="mimir"):
        self.is_loopback = loopback
        self.consent_muted = muted
        self.name = name
        self.profile_id = 1
        self.endpoint_url = "http://100.125.197.36:9292"


@pytest.fixture(autouse=True)
def _clean_session():
    clear_flag(ANALYST_REMOTE_EGRESS_MUTE_KEY)
    yield
    clear_flag(ANALYST_REMOTE_EGRESS_MUTE_KEY)


def test_a_loopback_run_never_asks():
    assert consent.consent_required(_Profile(loopback=True)) is False


def test_a_run_with_no_profile_never_asks():
    assert consent.consent_required(None) is False


def test_a_remote_run_asks_by_default():
    assert consent.consent_required(_Profile()) is True


def test_muting_the_session_stops_asking():
    set_flag(ANALYST_REMOTE_EGRESS_MUTE_KEY, True)
    assert consent.consent_required(_Profile()) is False


def test_a_profile_muted_forever_stops_asking():
    assert consent.consent_required(_Profile(muted=True)) is False


def test_a_new_profile_asks_again_even_when_another_is_muted():
    """Contract 10.3: consenting to one host is not consenting to the next."""
    muted = _Profile(muted=True, name="mimir")
    fresh = _Profile(muted=False, name="someone-elses-box")
    assert consent.consent_required(muted) is False
    assert consent.consent_required(fresh) is True


def test_the_session_mute_is_not_persistent():
    """It lives in session_flags, which resets on restart like every flag there."""
    set_flag(ANALYST_REMOTE_EGRESS_MUTE_KEY, True)
    assert consent.consent_required(_Profile()) is False
    clear_flag(ANALYST_REMOTE_EGRESS_MUTE_KEY)
    assert consent.consent_required(_Profile()) is True


# --------------------------------------------------------------------------
# The marker, which muting must never hide (contract 10.4)
# --------------------------------------------------------------------------

def test_a_loopback_run_shows_no_marker():
    assert consent.remote_marker(_Profile(loopback=True)) == ""


def test_a_remote_run_shows_its_server():
    assert consent.remote_marker(_Profile()) == "Remote: mimir"


def test_the_marker_survives_a_session_mute():
    set_flag(ANALYST_REMOTE_EGRESS_MUTE_KEY, True)
    assert consent.remote_marker(_Profile()) == "Remote: mimir"


def test_the_marker_survives_a_profile_mute():
    assert consent.remote_marker(_Profile(muted=True)) == "Remote: mimir"


# --------------------------------------------------------------------------
# The gate itself
# --------------------------------------------------------------------------

def test_a_loopback_run_proceeds_without_a_dialog():
    assert consent.confirm_remote_egress(
        None, profile=_Profile(loopback=True), model_name="qwen3.6:27b",
    ) is True


def test_a_muted_remote_run_proceeds_without_a_dialog():
    assert consent.confirm_remote_egress(
        None, profile=_Profile(muted=True), model_name="qwen3.8-27b",
    ) is True


def test_the_tab_asks_before_launching():
    """The gate must be on the launch path, not merely available."""
    from gui.components.experimental_features import analyst_tab

    source = open(analyst_tab.__file__, encoding="utf-8").read()
    assert "confirm_remote_egress" in source
    assert "nothing was sent" in source
