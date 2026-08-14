"""Offline command-boundary tests for C0B-6."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.analyst_benchmark import __main__ as package_main
from scripts.analyst_benchmark import c0b6_cli, leakscan


def test_package_entrypoint_dispatches_c0b6(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(c0b6_cli, "main", lambda args: calls.append(list(args)) or 7)
    assert package_main.main(["c0b6", "status", "--run-id", "run"]) == 7
    assert calls == [["status", "--run-id", "run"]]


@pytest.mark.parametrize("command", ["run", "resume"])
def test_live_confirmation_precedes_runtime_import(
        monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    module = "scripts.analyst_benchmark.c0b6_runtime"
    monkeypatch.delitem(sys.modules, module, raising=False)
    assert c0b6_cli.main([command, "--run-id", "run"]) == 2
    assert module not in sys.modules


def test_create_uses_isolated_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    fake = SimpleNamespace(
        create_confirmation_run=lambda: calls.append("create") or "child",
        render_public=lambda value: str(value),
    )
    monkeypatch.setitem(sys.modules, "scripts.analyst_benchmark.c0b6_runtime", fake)
    assert c0b6_cli.main(["create"]) == 0
    assert calls == ["create"]


def test_leak_scan_uses_exact_c0b6_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(leakscan, "run", lambda **kw: calls.append(kw) or 0)
    assert c0b6_cli.main([
        "leak-scan", "--baseline-file", "/tmp/base.json",
        "--raw-artifact", "/tmp/raw.jsonl",
    ]) == 0
    assert calls == [{
        "mode": "public", "baseline_path": Path("/tmp/base.json"),
        "raw_artifacts": [Path("/tmp/raw.jsonl")],
        "protocol_id": c0b6_cli.BENCHMARK_PROTOCOL_ID,
    }]


def test_verify_exit_and_errors_are_redacted(
        monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    fake = SimpleNamespace(
        confirmation_verify=lambda _run_id: {"ok": False},
        render_public=lambda value: str(value),
    )
    monkeypatch.setitem(sys.modules, "scripts.analyst_benchmark.c0b6_runtime", fake)
    assert c0b6_cli.main(["verify", "--run-id", "child"]) == 4
    assert "False" in capsys.readouterr().out

    def fail(_run_id):
        raise RuntimeError("/private/path raw response")

    fake.confirmation_status = fail
    assert c0b6_cli.main(["status", "--run-id", "child"]) == 4
    captured = capsys.readouterr()
    assert "/private/path" not in captured.err
    assert captured.err.strip() == "C0B-6 BLOCKED: status_failed"
