from __future__ import annotations

from pathlib import Path

import pytest

from experimental.analyst import inventory, service
from experimental.analyst.inventory import InventoryCancelled, InventoryResult
from experimental.analyst.service import DirectoryRunRequest


def test_inventory_tree_reports_periodic_progress(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    entry_count = inventory.INVENTORY_PROGRESS_INTERVAL * 2 + 1
    for index in range(entry_count):
        (root / f"{index:04d}.txt").touch()
    progress = []

    result = inventory.inventory_tree(root, progress_callback=progress.append)

    assert len(result.files) == entry_count
    assert progress == sorted(progress)
    assert progress == [
        inventory.INVENTORY_PROGRESS_INTERVAL,
        inventory.INVENTORY_PROGRESS_INTERVAL * 2,
    ]


def test_inventory_tree_honors_cancel_check(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "one.txt").touch()

    with pytest.raises(InventoryCancelled):
        inventory.inventory_tree(root, cancel_check=lambda: True)


def test_create_and_launch_forwards_inventory_callbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = DirectoryRunRequest(
        tmp_path / "source", tmp_path / "output", "Report", "fast",
    )
    cancel_check = lambda: False
    progress_callback = lambda _seen: None
    captured = {}
    expected_launch = object()
    captured_paths = object()

    def create(run_request, **kwargs):
        captured["create"] = (run_request, kwargs)
        return "a" * 32, InventoryResult(1, 2, 3, (), ())

    def launch(run_id, **kwargs):
        captured["launch"] = (run_id, kwargs)
        return expected_launch

    monkeypatch.setattr(service, "create_directory_run", create)
    monkeypatch.setattr(service, "launch_run", launch)

    result = service.create_and_launch(
        request,
        path=tmp_path / "analyst.db",
        paths=captured_paths,
        cancel_check=cancel_check,
        progress_callback=progress_callback,
    )

    assert result is expected_launch
    assert captured["create"] == (
        request,
        {
            "model_tag": None,
            "model_digest": None,
            "path": tmp_path / "analyst.db",
            "cancel_check": cancel_check,
            "progress_callback": progress_callback,
        },
    )
    assert captured["launch"] == (
        "a" * 32,
        {"path": tmp_path / "analyst.db", "paths": captured_paths},
    )
