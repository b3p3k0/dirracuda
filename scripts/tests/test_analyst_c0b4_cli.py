"""Offline command-boundary tests for C0B-4."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.analyst_benchmark import __main__ as package_main
from scripts.analyst_benchmark import c0b2_leakscan, c0b4_cli, leakscan


def test_package_entrypoint_dispatches_c0b4(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(c0b4_cli, "main", lambda args: calls.append(list(args)) or 7)
    assert package_main.main(["c0b4", "status", "--run-id", "run"]) == 7
    assert calls == [["status", "--run-id", "run"]]


@pytest.mark.parametrize("command", ["run", "resume"])
def test_live_confirmation_precedes_runtime_import(
        monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    module = "scripts.analyst_benchmark.c0b4_runtime"
    monkeypatch.delitem(sys.modules, module, raising=False)
    assert c0b4_cli.main([command, "--run-id", "run"]) == 2
    assert module not in sys.modules


def test_create_uses_isolated_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    fake = SimpleNamespace(
        create_confirmation_run=lambda: calls.append("create") or "child",
        render_public=lambda value: str(value),
    )
    monkeypatch.setitem(
        sys.modules, "scripts.analyst_benchmark.c0b4_runtime", fake)
    assert c0b4_cli.main(["create"]) == 0
    assert calls == ["create"]


def test_leak_scan_uses_exact_c0b4_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(leakscan, "run", lambda **kw: calls.append(kw) or 0)
    assert c0b4_cli.main([
        "leak-scan", "--baseline-file", "/tmp/base.json",
        "--raw-artifact", "/tmp/raw.jsonl",
    ]) == 0
    assert calls == [{
        "mode": "public", "baseline_path": Path("/tmp/base.json"),
        "raw_artifacts": [Path("/tmp/raw.jsonl")],
        "protocol_id": c0b4_cli.BENCHMARK_PROTOCOL_ID,
    }]


def test_three_frozen_allowlists_remain_distinct() -> None:
    assert len(c0b2_leakscan.FROZEN_C0B3_PUBLIC_PATHS) == 58
    assert len(c0b2_leakscan.FROZEN_C0B4_PUBLIC_PATHS) == 82
    assert leakscan.C0B4_ALLOWLIST_EXACT == set(
        c0b2_leakscan.FROZEN_C0B4_PUBLIC_PATHS)
    current = "scripts/analyst_benchmark/c0b4_policy.py"
    assert not leakscan.allowed(current)
    assert not leakscan.allowed(current, "c0b3-assistive-confirmation-v1")
    assert leakscan.allowed(current, c0b4_cli.BENCHMARK_PROTOCOL_ID)


def test_verify_exit_and_errors_are_redacted(monkeypatch: pytest.MonkeyPatch,
                                               capsys) -> None:
    fake = SimpleNamespace(
        confirmation_verify=lambda _run_id: {"ok": False},
        render_public=lambda value: str(value),
    )
    monkeypatch.setitem(
        sys.modules, "scripts.analyst_benchmark.c0b4_runtime", fake)
    assert c0b4_cli.main(["verify", "--run-id", "child"]) == 4
    assert "False" in capsys.readouterr().out

    def fail(_run_id):
        raise RuntimeError("/private/path raw response")

    fake.confirmation_status = fail
    assert c0b4_cli.main(["status", "--run-id", "child"]) == 4
    captured = capsys.readouterr()
    assert "/private/path" not in captured.err
