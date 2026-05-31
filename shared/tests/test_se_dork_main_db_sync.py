"""Unit tests for SearXNG primary-db run sync helper."""

from __future__ import annotations

from pathlib import Path

from experimental.se_dork.main_db_sync import _row_to_prefill, sync_run_to_main_db
from experimental.se_dork.models import RunOptions
from experimental.se_dork.store import init_db, insert_result, insert_run, open_connection
from gui.utils.sidecar_promotion import SidecarPromotionError


def _seed_run(db_path: Path, urls: list[str]) -> int:
    init_db(db_path)
    options = RunOptions(
        instance_url="http://searxng.local:8080",
        query='site:* intitle:"index of /"',
        max_results=100,
    )
    with open_connection(db_path) as conn:
        run_id = insert_run(conn, options, "2026-05-29T00:00:00")
        for url in urls:
            insert_result(
                conn,
                run_id,
                {
                    "url": url,
                    "title": "Index listing",
                    "content": "Parent directory",
                    "engine": "dummy",
                    "engines": ["dummy"],
                },
            )
        conn.commit()
    return run_id


def test_row_to_prefill_maps_http_and_normalizes_path() -> None:
    row = {
        "url": "https://Example.com:8443/public/files/?page=2#frag",
        "probe_status": "clean",
        "probe_indicator_matches": 0,
        "probe_preview": "public,files",
        "probe_checked_at": "2026-05-29T10:00:00",
        "probe_error": None,
    }

    prefill = _row_to_prefill(row)

    assert prefill is not None
    assert prefill["host_type"] == "H"
    assert prefill["host"] == "example.com"
    assert prefill["scheme"] == "https"
    assert prefill["port"] == 8443
    assert prefill["_probe_path_hint"] == "/public/files/"


def test_row_to_prefill_rejects_unsupported_or_hostless_urls() -> None:
    assert _row_to_prefill({"url": "ftp://1.2.3.4/files/"}) is None
    assert _row_to_prefill({"url": "http:///missing-host"}) is None


def test_sync_run_to_main_db_counts_shape_skips(tmp_path: Path) -> None:
    db_path = tmp_path / "main.db"
    run_id = _seed_run(
        db_path,
        [
            "http://1.2.3.4/open/",
            "ftp://1.2.3.4/public/",
            "http:///host-missing",
        ],
    )

    summary = sync_run_to_main_db(run_id, db_path=db_path)

    assert summary["selected"] == 3
    assert summary["processed"] == 3
    assert summary["inserted"] == 1
    assert summary["updated"] == 0
    assert summary["skipped"] == 2
    assert summary["failed"] == 0
    assert summary["cancelled"] == 0


def test_sync_run_to_main_db_counts_unresolved_host_as_skipped(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "main.db"
    run_id = _seed_run(db_path, ["http://unresolvable.invalid/open/"])

    def _raise_unresolved(host: str, *, resolver=None):  # pragma: no cover - invoked by helper
        raise SidecarPromotionError(f"Could not resolve '{host}' to an IPv4 address.")

    monkeypatch.setattr("gui.utils.sidecar_promotion.resolve_ipv4", _raise_unresolved)

    summary = sync_run_to_main_db(run_id, db_path=db_path)

    assert summary["selected"] == 1
    assert summary["processed"] == 1
    assert summary["inserted"] == 0
    assert summary["updated"] == 0
    assert summary["skipped"] == 1
    assert summary["failed"] == 0
    assert summary["cancelled"] == 0


def test_sync_run_to_main_db_is_idempotent_for_repeat_run(tmp_path: Path) -> None:
    db_path = tmp_path / "main.db"
    run_id = _seed_run(db_path, ["http://1.2.3.4/open/"])

    first = sync_run_to_main_db(run_id, db_path=db_path)
    second = sync_run_to_main_db(run_id, db_path=db_path)

    assert first["selected"] == 1
    assert first["inserted"] == 1
    assert first["updated"] == 0
    assert first["failed"] == 0
    assert first["cancelled"] == 0

    assert second["selected"] == 1
    assert second["inserted"] == 0
    assert second["updated"] == 1
    assert second["failed"] == 0
    assert second["cancelled"] == 0
