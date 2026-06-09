import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RESULTS_TEMPLATE = ROOT / "experimental" / "webui" / "templates" / "results.html"
RESULTS_SCRIPT = ROOT / "experimental" / "webui" / "static" / "results.js"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_results_table_keeps_probe_status_without_probe_action_column():
    template = _source(RESULTS_TEMPLATE)
    script = _source(RESULTS_SCRIPT)
    cells_block = script.split("var cells = [", 1)[1].split("];", 1)[0]
    data_cells = re.findall(r"\['([^']+)',", cells_block)

    assert "<th>Probed</th>" in template
    assert "<th>Probe</th>" not in template
    assert 'colspan="12"' in template
    assert len(data_cells) + 1 == 12
    assert "['Probe', 'Run']" not in script
    assert "probe-action-cell" not in script


def test_detail_actions_have_expected_labels_and_order():
    script = _source(RESULTS_SCRIPT)

    show_details = script.index("toggleBtn.textContent = 'Show Details'")
    open_system = script.index("openSystemBtn.textContent = 'Open with system'")
    run_probe = script.index("runProbeBtn.textContent = 'Run Probe'")

    assert show_details < open_system < run_probe
    assert "toggleBtn.textContent = isOpen ? 'Show Details' : 'Hide Details'" in script
    assert "full details + probe tree" not in script


def test_detail_probe_reuses_single_target_job_and_tracks_running_state():
    script = _source(RESULTS_SCRIPT)

    assert "runProbeBtn.disabled = probeRunning" in script
    assert "_performProbeAction([baseRow]);" in script
    assert "button.detail-run-probe" in script
    assert "setAttribute('data-detail-field', 'probe-status')" in script
    assert "_syncDetailProbeStatus(tr, state.probe_status);" in script
    assert "var probeStatus = baseRow ? _getProbeStatus(baseRow)" in script
