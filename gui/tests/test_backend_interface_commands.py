"""Command-building regression tests for backend config override plumbing."""

from pathlib import Path
from unittest.mock import patch

from gui.utils.backend_interface.interface import BackendInterface


def _config_arg_value(cmd: list[str]) -> str:
    idx = cmd.index("--config")
    return cmd[idx + 1]


def test_build_ftp_cli_command_includes_interface_config_path(tmp_path: Path) -> None:
    interface = BackendInterface(backend_path=str(tmp_path), mock_mode=True)

    cmd = interface._build_ftp_cli_command("--verbose", "--country", "US")

    assert "--config" in cmd
    assert _config_arg_value(cmd) == str(interface.config_path)


def test_build_cli_command_does_not_duplicate_explicit_config_arg(tmp_path: Path) -> None:
    interface = BackendInterface(backend_path=str(tmp_path), mock_mode=True)
    explicit = str(tmp_path / "custom.json")

    cmd = interface._build_cli_command("--verbose", "--config", explicit)

    assert cmd.count("--config") == 1
    assert _config_arg_value(cmd) == explicit


def test_temporary_override_updates_subprocess_config_path(tmp_path: Path) -> None:
    interface = BackendInterface(backend_path=str(tmp_path), mock_mode=True)
    original_path = str(interface.config_path)

    with interface._temporary_config_override({"ftp": {"verification": {"connect_timeout": 1}}}):
        cmd = interface._build_ftp_cli_command("--verbose")
        assert _config_arg_value(cmd) != original_path

    cmd_after = interface._build_ftp_cli_command("--verbose")
    assert _config_arg_value(cmd_after) == original_path


def test_run_ftp_scan_appends_filter_arg(tmp_path: Path) -> None:
    interface = BackendInterface(backend_path=str(tmp_path), mock_mode=True)
    interface.mock_mode = False

    with patch(
        "gui.utils.backend_interface.interface.process_runner.execute_with_progress",
        return_value={"success": True},
    ) as mock_exec:
        interface.run_ftp_scan(
            countries=["US"],
            filters='org:"Example ISP"',
            verbose=True,
        )

    cmd = mock_exec.call_args[0][1]
    assert "--filter" in cmd
    filter_idx = cmd.index("--filter")
    assert cmd[filter_idx + 1] == 'org:"Example ISP"'
