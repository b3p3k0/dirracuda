"""Registry + config-shard coverage for the Sherlock tab.

Kept in its own small file: test_experimental_features_dialog.py is already over
the project's 1700-line guardrail and must not grow.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.experimental_features.registry import _get_features
from shared.config_store import EXPERIMENTAL_MODULES, ConfigStore
from shared.sherlock import SHERLOCK_SETTINGS_KEY


def test_sherlock_feature_registered():
    features = _get_features()
    sherlock = [f for f in features if f.feature_id == "sherlock"]
    assert len(sherlock) == 1
    assert sherlock[0].label == "Sherlock"
    assert callable(sherlock[0].build_tab)


def test_sherlock_is_an_experimental_module():
    assert SHERLOCK_SETTINGS_KEY == "sherlock"
    assert "sherlock" in EXPERIMENTAL_MODULES


def test_sherlock_module_prefs_round_trip(tmp_path):
    store = ConfigStore.__new__(ConfigStore)
    store.conf_d_dir = tmp_path / "conf.d"
    payload = {"ignore_case": False, "builtin_disabled": ["cred_env"]}

    store.save_module_prefs("sherlock", payload)
    loaded = store.load_module_prefs("sherlock")

    assert loaded == payload
    assert (tmp_path / "conf.d" / "experimental" / "sherlock.json").exists()
