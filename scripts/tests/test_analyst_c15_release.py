from __future__ import annotations

import json
from pathlib import Path

import pytest

from experimental.analyst.report_browser import CompletedReportHandle
from experimental.analyst.worker import WorkerRunResult
from experimental.analyst.worker_contract import WorkerOutcome
from scripts import analyst_release_acceptance as release


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "a" * 32
SHA = "b" * 64


def test_installer_offers_exact_default_no_controlled_lane() -> None:
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    step = (ROOT / "scripts/install_scripts/step10_analyst.sh").read_text(
        encoding="utf-8",
    )
    assert installer.index("step9_webui.sh") < installer.index("step10_analyst.sh")
    assert "[10] Analyst document review setup      (optional)" in installer
    assert 'confirm "Install and verify optional Analyst dependencies?" "n"' in step
    assert "apt-get install -y bubblewrap antiword" in step
    assert "dpkg-query -W -f='${Version}' antiword" in step
    assert '!= "0.37-17"' in step
    assert "scripts/install_analyst_deps.py" in step
    assert "scripts/install_analyst_deps.py --check" in step
    assert "strict_preflight" in step
    assert "pip install" not in step
    assert "requirements-analyst" not in step
    for number in range(1, 11):
        matches = tuple((ROOT / "scripts/install_scripts").glob(f"step{number}_*.sh"))
        assert len(matches) == 1, number
        assert f"[Step {number} of 10]" in matches[0].read_text(encoding="utf-8")


@pytest.mark.parametrize("argv", [[], ["--confirm-live", "extra"], ["--confirm-live=yes"]])
def test_cli_refuses_without_exact_confirmation_before_any_action(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        release,
        "run_release_acceptance",
        lambda **_kwargs: pytest.fail("release acceptance ran without exact confirmation"),
    )
    assert release.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "refusing release acceptance without exact --confirm-live\n"


def test_direct_api_refuses_before_temporary_source_or_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        release.tempfile,
        "TemporaryDirectory",
        lambda **_kwargs: pytest.fail("temporary source created without confirmation"),
    )
    with pytest.raises(release.ReleaseAcceptanceError, match="live_confirmation_required"):
        release.run_release_acceptance(confirm_live=False)


def test_public_runner_uses_production_flow_and_emits_sanitized_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def create(request, *, path):
        observed["request"] = request
        observed["database"] = path
        source = request.source_root / "public-release-record.txt"
        assert source.read_text(encoding="utf-8") == release._PUBLIC_SOURCE
        assert request.mode == "fast"
        return RUN_ID, object()

    def run(run_id, stop_event, *, path):
        assert run_id == RUN_ID
        assert not stop_event.is_set()
        assert path == observed["database"]
        return WorkerRunResult(WorkerOutcome.COMPLETE)

    handle = CompletedReportHandle(
        RUN_ID, SHA, "Public synthetic release acceptance", "fast",
        "2026-08-16T12:00:00Z", "/temporary/redacted", 1, 0, 1, 1, 1, 2, 0,
    )

    def open_report(run_id, *, path):
        assert run_id == RUN_ID and path == observed["database"]
        return handle

    def html(run_id, *, path):
        assert run_id == RUN_ID and path == observed["database"]
        target = observed["request"].output_base / "report.html"
        target.write_text("public report", encoding="utf-8")
        return target

    monkeypatch.setattr(release, "create_directory_run", create)
    monkeypatch.setattr(release, "run_worker", run)
    monkeypatch.setattr(release, "open_completed_report", open_report)
    monkeypatch.setattr(release, "completed_report_html", html)

    evidence = release.run_release_acceptance(confirm_live=True)
    payload = json.loads(evidence.as_json())
    assert payload == {
        "protocol": "analyst-release-live-v1",
        "outcome": "complete",
        "run_id": RUN_ID,
        "report_manifest_sha256": SHA,
        "discovered_files": 1,
        "excluded_paths": 0,
        "detector_scanned_files": 1,
        "selected_files": 1,
        "model_reviewed_files": 1,
        "detector_hits": 2,
        "model_findings": 0,
    }
    serialized = evidence.as_json()
    assert "PUBLIC SYNTHETIC" not in serialized
    assert "temporary" not in serialized.casefold()
    assert "example.invalid" not in serialized


def test_resource_pause_is_explicit_and_claims_no_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        release, "create_directory_run", lambda *_args, **_kwargs: (RUN_ID, object()),
    )
    monkeypatch.setattr(
        release, "run_worker",
        lambda *_args, **_kwargs: WorkerRunResult(WorkerOutcome.PAUSED_RESOURCE),
    )
    monkeypatch.setattr(
        release,
        "open_completed_report",
        lambda *_args, **_kwargs: pytest.fail("paused run opened a report"),
    )
    evidence = release.run_release_acceptance(confirm_live=True)
    assert evidence.outcome == "inconclusive_resource"
    assert evidence.report_manifest_sha256 is None


def test_main_prints_only_one_sanitized_json_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    evidence = release.ReleaseEvidence(
        "complete", RUN_ID, SHA, 1, 0, 1, 1, 1, 1, 0,
    )
    monkeypatch.setattr(
        release, "run_release_acceptance", lambda *, confirm_live: evidence,
    )
    assert release.main(["--confirm-live"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == evidence.as_json() + "\n"
