from __future__ import annotations

import json
from pathlib import Path

from shared.config import SMBSeekConfig


def test_validate_configuration_expands_tilde_for_exclusion_file(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    exclusion_file = home / ".dirracuda" / "conf" / "exclusion_list.json"
    exclusion_file.parent.mkdir(parents=True, exist_ok=True)
    exclusion_file.write_text('{"organizations": []}', encoding="utf-8")

    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "shodan": {"api_key": "test-key"},
                "security": {"exclusion_file": "~/.dirracuda/conf/exclusion_list.json"},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(home))

    cfg = SMBSeekConfig(config_file=str(config_file))
    assert cfg.validate_configuration() is True

