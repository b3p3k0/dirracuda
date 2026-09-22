from __future__ import annotations

import json

import pytest

from experimental.analyst import service
from experimental.analyst.ollama_client import OllamaDiscoveryError
from experimental.analyst.ollama_contract import (
    MODEL_DIGEST,
    MODEL_TAG,
    DiscoveredModel,
    OllamaStatus,
    list_local_models,
)
from experimental.analyst.ollama_protocol import parse_tags_response
from experimental.analyst.service import AnalystServiceError, ServiceFailure
from experimental.analyst.store import open_connection


_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64


def _tags_body(*rows: tuple[str, str]) -> bytes:
    return json.dumps(
        {
            "models": [
                {"name": tag, "model": tag, "digest": digest, "size": 7}
                for tag, digest in rows
            ]
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _all(conn, sql: str):
    return conn.execute(sql).fetchall()


def test_list_local_models_returns_all_local_and_skips_cloud() -> None:
    models = list_local_models(
        _tags_body(
            ("zeta:7b", _DIGEST_C),
            ("remote:cloud", _DIGEST_A),
            ("alpha:3b", _DIGEST_A),
            ("vendor-cloud:9b", _DIGEST_B),
        )
    )

    assert models == (
        DiscoveredModel("alpha:3b", _DIGEST_A),
        DiscoveredModel("zeta:7b", _DIGEST_C),
    )


def test_discover_models_charges_finishes_and_upserts(
    tmp_path, monkeypatch,
) -> None:
    db_path = tmp_path / "analyst.db"
    responses = [
        (
            DiscoveredModel("alpha:3b", _DIGEST_A),
            DiscoveredModel("zeta:7b", _DIGEST_C),
        ),
        (
            DiscoveredModel("alpha:3b", _DIGEST_B),
            DiscoveredModel("zeta:7b", _DIGEST_C),
        ),
    ]

    class FakeClient:
        def __init__(self, *, endpoint=None, **kwargs):
            self.endpoint = endpoint

        def list_models(self):
            return responses.pop(0)

    real_finish = service.finish_discovery_contact
    finished_at = iter(("2026-09-18T12:00:01Z", "2026-09-18T12:00:02Z"))

    def deterministic_finish(contact_id, state, models_found, *, path=None):
        return real_finish(
            contact_id,
            state,
            models_found,
            now_utc=next(finished_at),
            path=path,
        )

    monkeypatch.setattr(service, "OllamaClient", FakeClient)
    monkeypatch.setattr(
        service, "finish_discovery_contact", deterministic_finish,
    )

    first = service.discover_models(path=db_path)
    second = service.discover_models(path=db_path)

    assert first[0].model_digest == _DIGEST_A
    assert second[0].model_digest == _DIGEST_B
    assert service.list_discovered_models(path=db_path) == second
    conn = open_connection(db_path, read_only=True)
    try:
        contacts = _all(
            conn,
            "SELECT contact_no,state,models_found,finished_at_utc "
            "FROM analyst_discovery_contact ORDER BY contact_no",
        )
        models = _all(
            conn,
            "SELECT model_tag,model_digest,first_seen_utc,last_seen_utc "
            "FROM analyst_discovered_model ORDER BY model_tag",
        )
    finally:
        conn.close()

    assert [tuple(row) for row in contacts] == [
        (1, "success", 2, "2026-09-18T12:00:01Z"),
        (2, "success", 2, "2026-09-18T12:00:02Z"),
    ]
    assert tuple(models[0]) == (
        "alpha:3b", _DIGEST_B,
        "2026-09-18T12:00:01Z", "2026-09-18T12:00:02Z",
    )
    assert tuple(models[1]) == (
        "zeta:7b", _DIGEST_C,
        "2026-09-18T12:00:01Z", "2026-09-18T12:00:02Z",
    )


def test_discover_models_transport_failure_is_finished_and_content_free(
    tmp_path, monkeypatch,
) -> None:
    db_path = tmp_path / "analyst.db"
    secret = "private-model-or-source-content"

    class FakeClient:
        def list_models(self):
            try:
                raise ConnectionError(secret)
            except ConnectionError:
                raise OllamaDiscoveryError(
                    OllamaStatus.TRANSPORT_UNAVAILABLE
                ) from None

    monkeypatch.setattr(service, "OllamaClient", FakeClient)

    with pytest.raises(AnalystServiceError) as caught:
        service.discover_models(path=db_path)

    assert caught.value.code is ServiceFailure.DISCOVERY
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    conn = open_connection(db_path, read_only=True)
    try:
        contact = conn.execute(
            "SELECT state,models_found,finished_at_utc "
            "FROM analyst_discovery_contact"
        ).fetchone()
        model_count = conn.execute(
            "SELECT count(*) FROM analyst_discovered_model"
        ).fetchone()[0]
    finally:
        conn.close()

    assert tuple(contact)[:2] == ("transport_unavailable", None)
    assert contact["finished_at_utc"] is not None
    assert model_count == 0


def test_strict_tags_parser_still_returns_only_expected_model() -> None:
    body = _tags_body(
        (MODEL_TAG, MODEL_DIGEST),
        ("unrelated:7b", _DIGEST_A),
    )

    parsed = parse_tags_response(body, {MODEL_TAG: MODEL_DIGEST})

    assert tuple((item.model, item.digest) for item in parsed.models) == (
        (MODEL_TAG, MODEL_DIGEST),
    )
