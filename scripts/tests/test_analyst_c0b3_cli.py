"""Focused namespace and confirmation tests for the C0B-3 CLI."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.analyst_benchmark import __main__ as package_main
from scripts.analyst_benchmark import c0b3_cli
from scripts.analyst_benchmark import c0b2_leakscan, leakscan
from scripts.analyst_benchmark.c0b3_policy import BENCHMARK_PROTOCOL_ID


def test_package_entrypoint_dispatches_c0b3(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[list[str]] = []
    monkeypatch.setattr(c0b3_cli, "main", lambda args: called.append(list(args)) or 7)
    assert package_main.main(["c0b3", "status", "--run-id", "run"]) == 7
    assert called == [["status", "--run-id", "run"]]


@pytest.mark.parametrize("command", ["run", "resume"])
def test_live_confirmation_fails_before_runtime_import(
        monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    monkeypatch.delitem(sys.modules, "scripts.analyst_benchmark.c0b2_runtime", raising=False)
    assert c0b3_cli.main([command, "--run-id", "run", "--stage", "C"]) == 2
    assert "scripts.analyst_benchmark.c0b2_runtime" not in sys.modules


def test_create_passes_exact_current_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    fake = SimpleNamespace(
        create_public_run=lambda **kw: calls.append(kw) or "c0b3-run",
        render_public=lambda value: str(value),
    )
    monkeypatch.setitem(sys.modules, "scripts.analyst_benchmark.c0b2_runtime", fake)
    assert c0b3_cli.main(["create"]) == 0
    assert calls == [{"protocol_id": BENCHMARK_PROTOCOL_ID}]


def test_leak_scan_uses_exact_current_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(leakscan, "run", lambda **kw: calls.append(kw) or 0)
    assert c0b3_cli.main([
        "leak-scan", "--baseline-file", "/tmp/base.json",
        "--raw-artifact", "/tmp/raw.jsonl",
    ]) == 0
    assert calls == [{
        "mode": "public", "baseline_path": Path("/tmp/base.json"),
        "raw_artifacts": [Path("/tmp/raw.jsonl")],
        "protocol_id": BENCHMARK_PROTOCOL_ID,
    }]


def test_protocol_scoped_leak_allowlists_are_exact() -> None:
    assert leakscan.ALLOWLIST_EXACT == set(c0b2_leakscan.FROZEN_C0B2_PUBLIC_PATHS)
    assert leakscan.C0B3_ALLOWLIST_EXACT == set(
        c0b2_leakscan.FROZEN_C0B3_PUBLIC_PATHS)
    assert (len(leakscan.ALLOWLIST_EXACT), len(leakscan.C0B3_ALLOWLIST_EXACT)) == (48, 58)
    current_only = "scripts/analyst_benchmark/c0b3_policy.py"
    assert not leakscan.allowed(current_only)
    assert leakscan.allowed(current_only, BENCHMARK_PROTOCOL_ID)
    assert not leakscan.allowed("unexpected/private.txt", BENCHMARK_PROTOCOL_ID)


@pytest.mark.parametrize("stage,module_name,function_name", [
    ("C", "scripts.analyst_benchmark.c0b2_runtime", "run_public_stage_c"),
    ("D", "scripts.analyst_benchmark.c0b2_runtime_d", "run_public_stage_d"),
    ("F", "scripts.analyst_benchmark.c0b2_runtime_f", "run_public_stage_f"),
])
def test_live_commands_pass_exact_expected_protocol(
        monkeypatch: pytest.MonkeyPatch, stage: str, module_name: str,
        function_name: str) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def run(run_id: str, **kwargs: object) -> dict[str, str]:
        calls.append((run_id, kwargs))
        return {"state": "PAUSED"}

    runtime = SimpleNamespace(
        render_public=lambda value: str(value), run_public_stage_c=run)
    monkeypatch.setitem(sys.modules, "scripts.analyst_benchmark.c0b2_runtime", runtime)
    if stage != "C":
        monkeypatch.setitem(sys.modules, module_name, SimpleNamespace(**{function_name: run}))
    assert c0b3_cli.main([
        "run", "--run-id", "c0b3-run", "--stage", stage, "--confirm-live",
    ]) == 0
    assert calls == [("c0b3-run", {
        "resume": False, "expected_protocol_id": BENCHMARK_PROTOCOL_ID})]


def test_status_and_verify_remain_read_only_cross_family(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    runtime = SimpleNamespace(
        public_status=lambda run_id: calls.append("status:" + run_id) or {"state": "X"},
        public_verify=lambda run_id: calls.append("verify:" + run_id) or {"ok": True},
        render_public=lambda value: str(value),
    )
    monkeypatch.setitem(sys.modules, "scripts.analyst_benchmark.c0b2_runtime", runtime)
    assert c0b3_cli.main(["status", "--run-id", "legacy"]) == 0
    assert c0b3_cli.main(["verify", "--run-id", "legacy"]) == 0
    assert calls == ["status:legacy", "verify:legacy"]
