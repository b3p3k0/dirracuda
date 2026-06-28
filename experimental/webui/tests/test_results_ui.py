import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RESULTS_TEMPLATE = ROOT / "experimental" / "webui" / "templates" / "results.html"
RESULTS_SCRIPT = ROOT / "experimental" / "webui" / "static" / "results.js"
RESULTS_STYLE = ROOT / "experimental" / "webui" / "static" / "style.css"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_results_table_keeps_probe_status_without_probe_action_column():
    template = _source(RESULTS_TEMPLATE)
    script = _source(RESULTS_SCRIPT)
    cells_block = script.split("var cells = [", 1)[1].split("];", 1)[0]
    data_cells = re.findall(r"\['([^']+)',", cells_block)

    assert "<th>Probed</th>" in template
    assert "<th>Probe</th>" not in template
    assert 'colspan="13"' in template
    assert len(data_cells) + 1 == 13
    assert "['Probe', 'Run']" not in script
    assert "probe-action-cell" not in script


def test_results_table_has_readonly_sherlock_risk_column():
    template = _source(RESULTS_TEMPLATE)
    script = _source(RESULTS_SCRIPT)
    style = _source(RESULTS_STYLE)

    # Risk column placed between Extracted and Type (desktop order).
    assert "<th>Extracted</th>\n        <th>Risk</th>\n        <th>Type</th>" in template

    # Row badge cell + read-only detail block rendered from persisted data.
    assert "['Risk', r.sherlock_risk]" in script
    assert "_renderRiskCell(td, pair[1]);" in script
    assert "_renderSherlockDetail(container, payload.sherlock);" in script
    # Quiet contract: blank when no fresh finding; uses textContent (no innerHTML).
    assert "if (!risk || !risk.text) return;" in script
    assert "badge.textContent = risk.text;" in script

    # No editing / scan controls in the Web UI Sherlock surface.
    assert "sherlock-scan" not in script
    assert "sherlock-edit" not in script

    assert ".sherlock-badge" in style


def test_sherlock_hit_user_tag_label_rendered_via_textcontent():
    script = _source(RESULTS_SCRIPT)

    # C11: per-hit user tag label appended to the hit line via textContent only
    # (string concatenation, never innerHTML) so no value is interpreted as HTML.
    assert "_userTagLabel(hit.color_tag)" in script
    assert "var tag = _userTagLabel(hit.color_tag);" in script
    assert "li.textContent = sev + ' · ' + cat + ' · ' + lbl + pat + tag + ' — ' + path;" in script
    assert "' [User' + token.charAt(4) + ']'" in script

    # The Web UI remains read-only: no scan/edit surface introduced by the label.
    assert "sherlock-scan" not in script
    assert "sherlock-edit" not in script


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
