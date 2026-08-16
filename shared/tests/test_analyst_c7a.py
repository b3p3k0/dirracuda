"""C7A sandboxed legacy Word extraction contracts."""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from experimental.analyst import extract, legacy_child
from experimental.analyst.extract import ExtractionResult, extract_document
from experimental.analyst.formats import DocumentFormat, sniff_document_format
from experimental.analyst.legacy_contract import (
    ANTIWORD_PACKAGE_REVISION,
    ANTIWORD_VERSION,
    FRAME_MAGIC,
    MAX_LOGICAL_UNITS,
    UNIT_SEPARATOR,
)
from experimental.analyst.legacy_frame import decode_legacy_frame
from experimental.analyst.models import FileTerminal
from experimental.analyst.sandbox import SandboxResult, _inventory_for_fd
from experimental.analyst.xls_contract import (
    CALAMINE_VERSION,
    FRAME_MAGIC as XLS_FRAME_MAGIC,
    PYTHON_CALAMINE_VERSION,
)


def _frame(header: dict, body: bytes = b"") -> bytes:
    return FRAME_MAGIC + json.dumps(
        header, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii") + b"\n" + body


def _header(**changes) -> dict:
    body = "alpha\fβeta"
    header = {
        "antiword_version": ANTIWORD_VERSION,
        "detail": None,
        "format": "doc",
        "logical_unit_count": 2,
        "package_revision": ANTIWORD_PACKAGE_REVISION,
        "status": "success",
        "text_bytes": len(body.encode("utf-8")),
        "text_chars": len(body),
        "units": [
            {"kind": "output_line", "label": "output-line-1", "text_chars": 5},
            {"kind": "output_line", "label": "output-line-3", "text_chars": 4},
        ],
    }
    header.update(changes)
    return header


def _failure_header(status: str, detail: str, **changes) -> dict:
    header = {
        "antiword_version": ANTIWORD_VERSION,
        "detail": detail,
        "format": "doc",
        "logical_unit_count": 0,
        "package_revision": ANTIWORD_PACKAGE_REVISION,
        "status": status,
        "text_bytes": 0,
        "text_chars": 0,
        "units": [],
    }
    header.update(changes)
    return header


def _extract_path(path: Path) -> ExtractionResult:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        return extract_document(source_fd=fd, expected=_inventory_for_fd(fd))
    finally:
        os.close(fd)


def test_cfb_magic_routes_only_to_a_legacy_office_candidate() -> None:
    signature = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    assert sniff_document_format(signature) is DocumentFormat.LEGACY_OFFICE
    assert sniff_document_format(signature + b"renamed-without-extension") is \
        DocumentFormat.LEGACY_OFFICE
    assert sniff_document_format(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a") is None


def test_strict_legacy_frame_preserves_honest_output_line_provenance() -> None:
    body = "alpha\fβeta".encode()
    decoded = decode_legacy_frame(
        _frame(_header(), body), max_text_bytes=100, max_text_chars=100,
    )
    assert decoded.reason == "success"
    assert decoded.format_name == "doc"
    assert decoded.text == "alpha\fβeta"
    assert decoded.logical_unit_count == 2
    assert decoded.parser_version == ANTIWORD_VERSION
    assert decoded.package_revision == ANTIWORD_PACKAGE_REVISION
    assert [(unit.kind, unit.label, unit.char_count) for unit in decoded.units] == [
        ("output_line", "output-line-1", 5),
        ("output_line", "output-line-3", 4),
    ]


@pytest.mark.parametrize(
    ("status", "detail", "reason"),
    [
        ("encrypted", "password_required", FileTerminal.ENCRYPTED.value),
        ("parse_error", "antiword_failed", FileTerminal.PARSE_ERROR.value),
        ("parse_error", "control_character", FileTerminal.PARSE_ERROR.value),
        ("parse_error", "text_decode", FileTerminal.PARSE_ERROR.value),
        ("parse_oom", "memory_limit", FileTerminal.PARSE_OOM.value),
        (
            "parser_output_limit", "semantic_unit_limit",
            FileTerminal.PARSER_OUTPUT_LIMIT.value,
        ),
        (
            "parser_output_limit", "stderr_limit",
            FileTerminal.PARSER_OUTPUT_LIMIT.value,
        ),
        (
            "parser_output_limit", "text_limit",
            FileTerminal.PARSER_OUTPUT_LIMIT.value,
        ),
        (
            "unsupported_format", "not_word_binary",
            FileTerminal.UNSUPPORTED_FORMAT.value,
        ),
        (
            "unsupported_format", "unsupported_word_variant",
            FileTerminal.UNSUPPORTED_FORMAT.value,
        ),
    ],
)
def test_strict_legacy_failure_vocabulary(
    status: str, detail: str, reason: str,
) -> None:
    decoded = decode_legacy_frame(
        _frame(_failure_header(status, detail)),
        max_text_bytes=100,
        max_text_chars=100,
    )
    assert (decoded.reason, decoded.detail, decoded.text) == (reason, detail, None)


@pytest.mark.parametrize(
    ("header", "body"),
    [
        (_header(antiword_version="0.36"), b"alpha\f\xce\xb2eta"),
        (_header(package_revision="0.37-16"), b"alpha\f\xce\xb2eta"),
        (_header(format="xls"), b"alpha\f\xce\xb2eta"),
        (_header(logical_unit_count=True), b"alpha\f\xce\xb2eta"),
        (_header(logical_unit_count=1), b"alpha\f\xce\xb2eta"),
        (_header(text_bytes=True), b"alpha\f\xce\xb2eta"),
        (_header(text_chars=8), b"alpha\f\xce\xb2eta"),
        (_header(text_chars=101), b"alpha\f\xce\xb2eta"),
        (_header(text_bytes=101), b"alpha\f\xce\xb2eta"),
        (_header(detail="antiword_failed"), b"alpha\f\xce\xb2eta"),
        (_header(extra="nope"), b"alpha\f\xce\xb2eta"),
        (
            _header(units=[
                {"kind": "paragraph", "label": "output-line-1", "text_chars": 5},
                {"kind": "output_line", "label": "output-line-3", "text_chars": 4},
            ]),
            b"alpha\f\xce\xb2eta",
        ),
        (
            _header(units=[
                {"kind": "output_line", "label": "output-line-01", "text_chars": 5},
                {"kind": "output_line", "label": "output-line-3", "text_chars": 4},
            ]),
            b"alpha\f\xce\xb2eta",
        ),
        (
            _header(units=[
                {"kind": "output_line", "label": "output-line-3", "text_chars": 5},
                {"kind": "output_line", "label": "output-line-2", "text_chars": 4},
            ]),
            b"alpha\f\xce\xb2eta",
        ),
        (
            _header(units=[
                {"kind": "output_line", "label": "output-line-1", "text_chars": 5},
                {"kind": "output_line", "label": "output-line-3", "text_chars": 3},
            ]),
            b"alpha\f\xce\xb2eta",
        ),
        (_header(), b"alpha\n\xce\xb2eta"),
        (_header(), b"alpha\x00\xce\xb2eta"),
        (_header(), b"alpha\xff\xce\xb2eta"),
    ],
)
def test_strict_legacy_success_frame_fails_closed(header: dict, body: bytes) -> None:
    decoded = decode_legacy_frame(
        _frame(header, body), max_text_bytes=100, max_text_chars=100,
    )
    assert decoded.reason == FileTerminal.PARSE_ERROR.value
    assert decoded.text is None and decoded.units == ()


def test_failure_frames_cannot_smuggle_text_units_or_unknown_details() -> None:
    invalid = [
        (_failure_header("parse_error", "made_up"), b""),
        (_failure_header("success", "antiword_failed"), b""),
        (_failure_header("parse_error", "antiword_failed", text_bytes=1), b"x"),
        (_failure_header("parse_error", "antiword_failed", text_chars=1), b""),
        (
            _failure_header(
                "parse_error", "antiword_failed", logical_unit_count=1,
                units=[{
                    "kind": "output_line", "label": "output-line-1",
                    "text_chars": 1,
                }],
            ),
            b"",
        ),
        (_failure_header("parse_error", "antiword_failed", package_revision="x"), b""),
    ]
    for header, body in invalid:
        decoded = decode_legacy_frame(
            _frame(header, body), max_text_bytes=100, max_text_chars=100,
        )
        assert decoded.reason == FileTerminal.PARSE_ERROR.value
        assert decoded.detail is None


def test_duplicate_keys_and_seeded_hostile_frames_never_escape_decoder() -> None:
    duplicate = FRAME_MAGIC + (
        b'{"antiword_version":"0.37","antiword_version":"0.37"}\n'
    )
    assert decode_legacy_frame(
        duplicate, max_text_bytes=100, max_text_chars=100,
    ).reason == FileTerminal.PARSE_ERROR.value

    rng = random.Random(20260815)
    for _ in range(1000):
        payload = rng.randbytes(rng.randrange(0, 1025))
        decoded = decode_legacy_frame(
            payload, max_text_bytes=100, max_text_chars=100,
        )
        assert decoded.reason == FileTerminal.PARSE_ERROR.value


def test_blank_authenticated_document_frame_is_a_real_success() -> None:
    decoded = decode_legacy_frame(
        _frame(_header(
            logical_unit_count=0, text_bytes=0, text_chars=0, units=[],
        )),
        max_text_bytes=100,
        max_text_chars=100,
    )
    assert decoded.reason == "success" and decoded.text == ""
    assert decoded.units == () and decoded.logical_unit_count == 0


def test_child_output_tracks_physical_lines_and_exact_separator_budget() -> None:
    output = legacy_child._normalize_output(
        b"first\r\n\r\nthird\tvalue\n", max_bytes=18, max_chars=18,
    )
    assert output.finish() == "first\fthird\tvalue"
    assert [(unit.label, unit.text) for unit in output.units] == [
        ("output-line-1", "first"), ("output-line-3", "third\tvalue"),
    ]
    assert output.byte_count == 17 and output.char_count == 17

    exact = legacy_child.Output(3, 3)
    exact.add(1, "a")
    exact.add(2, "b")
    assert exact.finish() == "a\fb"
    with pytest.raises(legacy_child.ChildFailure, match="text_limit"):
        exact.add(3, "c")


def test_child_rejects_decode_controls_and_unit_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for payload in (b"\xff", b"safe\x00bad", b"safe\x7fbad"):
        with pytest.raises(legacy_child.ChildFailure):
            legacy_child._normalize_output(payload, 100, 100)

    monkeypatch.setattr(legacy_child, "MAX_LOGICAL_UNITS", 1)
    output = legacy_child.Output(100, 100)
    output.add(1, "one")
    with pytest.raises(legacy_child.ChildFailure, match="semantic_unit_limit"):
        output.add(2, "two")


def test_child_repairs_only_paired_antiword_cesu8_surrogates() -> None:
    # Antiword 0.37 emits supplementary Unicode scalars as CESU-8 surrogate
    # pairs even when its selected mapping is named UTF-8.
    grinning_face_cesu8 = b"\xed\xa0\xbd\xed\xb8\x80"
    emoji = chr(0x1F600)
    assert legacy_child._decode_antiword_utf8(grinning_face_cesu8) == emoji
    assert legacy_child._decode_antiword_utf8(emoji.encode()) == emoji
    for malformed in (
        b"\xed\xa0\xbd", b"\xed\xb8\x80", b"\xed\xa0\xbdx",
        b"\xed\xa0\xbd\xed\xa0\xbd", b"\xed\xb8\x80\xed\xa0\xbd",
    ):
        with pytest.raises(legacy_child.ChildFailure, match="text_decode"):
            legacy_child._decode_antiword_utf8(malformed)


def test_child_physical_line_splitter_does_not_treat_unicode_as_structure() -> None:
    assert list(legacy_child._physical_lines("one\u2028two\none\fthree\r\nfour")) == [
        (1, "one\u2028two"), (2, "one"), (3, "three"), (4, "four"),
    ]


@pytest.mark.parametrize(
    ("returncode", "stderr", "status", "detail"),
    [
        (1, b"Encrypted documents are not supported", "encrypted", "password_required"),
        (1, b"x is not a Word Document.", "unsupported_format", "not_word_binary"),
        (
            1, b"fast saved documents are not supported",
            "unsupported_format", "unsupported_word_variant",
        ),
        (-9, b"is not a Word Document.", "parse_error", "antiword_failed"),
        (137, b"Encrypted documents are not supported", "parse_error", "antiword_failed"),
        (1, b"unknown diagnostic", "parse_error", "antiword_failed"),
    ],
)
def test_child_maps_only_closed_exact_antiword_diagnostics(
    returncode: int, stderr: bytes, status: str, detail: str,
) -> None:
    failure = legacy_child._failure_from_result(returncode, stderr)
    assert (failure.status, failure.detail) == (status, detail)


def test_child_discards_partial_output_on_failure(
    monkeypatch: pytest.MonkeyPatch, capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    monkeypatch.setattr(
        legacy_child,
        "_capture_antiword",
        lambda _limit: (1, b"partial secret text", b"unknown diagnostic"),
    )
    assert legacy_child.main(["100", "100"]) == 0
    payload = capfdbinary.readouterr().out
    decoded = decode_legacy_frame(
        payload, max_text_bytes=100, max_text_chars=100,
    )
    assert (decoded.reason, decoded.detail) == (
        FileTerminal.PARSE_ERROR.value, "antiword_failed",
    )
    assert decoded.text is None and b"partial secret text" not in payload


def test_child_rejects_success_with_any_stderr_and_discards_stdout(
    monkeypatch: pytest.MonkeyPatch, capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    monkeypatch.setattr(
        legacy_child,
        "_capture_antiword",
        lambda _limit: (0, b"apparently valid text", b"unexpected warning"),
    )
    assert legacy_child.main(["100", "100"]) == 0
    payload = capfdbinary.readouterr().out
    decoded = decode_legacy_frame(
        payload, max_text_bytes=100, max_text_chars=100,
    )
    assert (decoded.reason, decoded.detail) == (
        FileTerminal.PARSE_ERROR.value, "antiword_failed",
    )
    assert decoded.text is None and b"apparently valid text" not in payload


def test_child_uses_exact_antiword_command_and_scrubbed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = _FakeStream(10)
    stderr = _FakeStream(11)
    process = _FakeProcess(stdout, stderr)
    observed: dict[str, object] = {}

    def popen(command, **kwargs):
        observed.update(command=command, **kwargs)
        return process

    chunks = {10: [b"public", b""], 11: [b"", b""]}
    monkeypatch.setattr(legacy_child.subprocess, "Popen", popen)
    monkeypatch.setattr(legacy_child.selectors, "DefaultSelector", _FakeSelector)
    monkeypatch.setattr(legacy_child.os, "read", lambda fd, _size: chunks[fd].pop(0))

    returncode, captured_out, captured_err = legacy_child._capture_antiword(100)
    assert (returncode, captured_out, captured_err) == (0, b"public", b"")
    assert observed["command"] == (
        "/runtime/antiword", "-t", "-w", "0", "-m", "UTF-8.txt",
        "-r", "-s", "/input/document",
    )
    assert observed["cwd"] == "/tmp"
    assert observed["env"] == {
        "ANTIWORDHOME": "/runtime/antiword-data",
        "HOME": "/sandbox-home",
        "LANG": "C",
        "LC_ALL": "C",
        "TMPDIR": "/tmp",
    }
    assert observed["stdin"] is legacy_child.subprocess.DEVNULL
    assert observed["close_fds"] is True


def test_child_kills_parser_and_emits_no_partial_capture_over_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = _FakeStream(20)
    stderr = _FakeStream(21)
    process = _FakeProcess(stdout, stderr)
    chunks = {20: [b"12345", b""], 21: [b"", b""]}
    monkeypatch.setattr(legacy_child.subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(legacy_child.selectors, "DefaultSelector", _FakeSelector)
    monkeypatch.setattr(legacy_child.os, "read", lambda fd, _size: chunks[fd].pop(0))

    with pytest.raises(legacy_child.ChildFailure, match="text_limit"):
        legacy_child._capture_antiword(4)
    assert process.killed and process.waited


def test_legacy_route_uses_exact_child_and_decodes_strict_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "renamed.bin"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1candidate")
    body = "alpha\fβeta".encode()
    observed: dict[str, object] = {}
    monkeypatch.setattr(extract, "antiword_runtime_binds", lambda: ())

    def run_sandboxed(**kwargs):
        observed.update(kwargs)
        return SandboxResult(
            "success", 0, _frame(_header(), body), b"", "test.scope",
        )

    monkeypatch.setattr(extract, "run_sandboxed", run_sandboxed)
    result = _extract_path(path)

    assert result.ok and result.format_name == "doc"
    assert result.encoding == "utf-8" and result.text == "alpha\fβeta"
    assert result.parser_version == ANTIWORD_VERSION
    assert result.package_revision == ANTIWORD_PACKAGE_REVISION
    assert result.logical_unit_count == 2 and len(result.legacy_units) == 2
    assert observed["command"] == (
        str(Path(sys.executable).resolve()), "-I", "-B",
        str(extract.LEGACY_CHILD_DESTINATION), str(extract.MAX_TEXT_BYTES),
        str(extract.MAX_TEXT_CHARS),
    )
    assert observed["runtime_binds"] == ()
    assert observed["limits"].stdout_bytes == (
        extract.MAX_TEXT_BYTES + extract.MAX_LEGACY_HEADER_BYTES
        + len(extract.LEGACY_FRAME_MAGIC) + 1
    )


@pytest.mark.parametrize(
    ("detail", "exception"),
    [
        ("dependency_missing", extract.OptionalDependencyUnavailable),
        ("dependency_version", extract.OptionalDependencyUnavailable),
    ],
)
def test_missing_or_wrong_antiword_still_checks_xls_then_retains_doc_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    detail: str,
    exception: type[Exception],
) -> None:
    path = tmp_path / "candidate.ole"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1candidate")

    def unavailable():
        raise exception(detail)

    monkeypatch.setattr(extract, "antiword_runtime_binds", unavailable)
    monkeypatch.setattr(extract, "xls_runtime_binds", lambda: ())
    observed: dict[str, object] = {}

    def sandbox(**kwargs):
        observed.update(kwargs)
        header = {
            "calamine_version": CALAMINE_VERSION,
            "dense_cell_count": 0,
            "detail": "not_xls",
            "format": "xls",
            "logical_unit_count": 0,
            "python_calamine_version": PYTHON_CALAMINE_VERSION,
            "sheet_count": 0,
            "skipped_sheet_count": 0,
            "status": "unsupported_format",
            "text_bytes": 0,
            "text_chars": 0,
            "units": [],
            "worksheet_count": 0,
        }
        payload = XLS_FRAME_MAGIC + json.dumps(
            header, sort_keys=True, separators=(",", ":"),
        ).encode("ascii") + b"\n"
        return SandboxResult("success", 0, payload, b"", "test.scope")

    monkeypatch.setattr(extract, "run_sandboxed", sandbox)
    result = _extract_path(path)
    assert (result.reason, result.format_name, result.detail) == (
        FileTerminal.SANDBOX_UNAVAILABLE.value,
        DocumentFormat.LEGACY_OFFICE.value,
        detail,
    )
    assert observed["runtime_binds"] == ()
    assert observed["command"][3] == str(extract.XLS_CHILD_DESTINATION)


def test_antiword_package_probe_is_exact_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[object, dict[str, object]]] = []
    replies = iter((
        SimpleNamespace(
            returncode=0,
            stdout=f"installed\n{ANTIWORD_PACKAGE_REVISION}\n".encode(),
            stderr=b"",
        ),
        SimpleNamespace(
            returncode=0,
            stdout=b"",
            stderr=(
                f"\tVersion: {ANTIWORD_VERSION}  (21 Oct 2005)\n".encode()
            ),
        ),
    ))

    def run(command, **kwargs):
        observed.append((command, kwargs))
        return next(replies)

    monkeypatch.setattr(extract.subprocess, "run", run)
    extract._verify_antiword_package()
    assert observed[0][0] == [
        "/usr/bin/dpkg-query", "-W",
        r"-f=${db:Status-Status}\n${Version}\n", "antiword",
    ]
    assert observed[1][0] == ["/usr/bin/antiword", "-h"]
    for _command, kwargs in observed:
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.PIPE
        assert kwargs["timeout"] == 5
        assert kwargs["check"] is False and kwargs["shell"] is False
        assert kwargs["env"] == {
            "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin",
        }


def test_antiword_package_probe_rejects_revision_and_upstream_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_revision = SimpleNamespace(
        returncode=0, stdout=b"installed\n0.37-16\n", stderr=b"",
    )
    monkeypatch.setattr(extract.subprocess, "run", lambda *_a, **_k: wrong_revision)
    with pytest.raises(extract.OptionalDependencyUnavailable) as caught:
        extract._verify_antiword_package()
    assert caught.value.detail == "dependency_version"

    warning = SimpleNamespace(
        returncode=0,
        stdout=f"installed\n{ANTIWORD_PACKAGE_REVISION}\n".encode(),
        stderr=b"warning\n",
    )
    monkeypatch.setattr(extract.subprocess, "run", lambda *_a, **_k: warning)
    with pytest.raises(extract.OptionalDependencyUnavailable) as caught:
        extract._verify_antiword_package()
    assert caught.value.detail == "dependency_version"

    replies = iter((
        SimpleNamespace(
            returncode=0,
            stdout=f"installed\n{ANTIWORD_PACKAGE_REVISION}\n".encode(),
            stderr=b"",
        ),
        SimpleNamespace(
            returncode=0, stdout=b"", stderr=b"Version: 0.38 fake\n",
        ),
    ))
    monkeypatch.setattr(extract.subprocess, "run", lambda *_a, **_k: next(replies))
    with pytest.raises(extract.OptionalDependencyUnavailable) as caught:
        extract._verify_antiword_package()
    assert caught.value.detail == "dependency_version"

    canonical = f"\tVersion: {ANTIWORD_VERSION}  (21 Oct 2005)\n".encode()
    for forged in (
        b"prefix " + canonical,
        canonical + canonical,
        canonical.replace(b"21 Oct 2005", b"22 Oct 2005"),
    ):
        replies = iter((
            SimpleNamespace(
                returncode=0,
                stdout=f"installed\n{ANTIWORD_PACKAGE_REVISION}\n".encode(),
                stderr=b"",
            ),
            SimpleNamespace(returncode=0, stdout=b"", stderr=forged),
        ))
        monkeypatch.setattr(
            extract.subprocess, "run", lambda *_a, **_k: next(replies)
        )
        with pytest.raises(extract.OptionalDependencyUnavailable) as caught:
            extract._verify_antiword_package()
        assert caught.value.detail == "dependency_version"


def test_antiword_runtime_bind_is_narrow_and_exact() -> None:
    bindings = extract.antiword_runtime_binds()
    sources = {binding.source for binding in bindings}
    destinations = {binding.destination for binding in bindings}
    assert extract.ANTIWORD_HOST_PATH in sources
    assert extract.ANTIWORD_DATA_HOST_PATH in sources
    assert extract.LEGACY_CHILD_PATH in sources
    assert extract.LEGACY_CONTRACT_PATH in sources
    assert extract.ANTIWORD_RUNTIME_PATH in destinations
    assert extract.ANTIWORD_DATA_RUNTIME_PATH in destinations
    assert extract.LEGACY_CHILD_DESTINATION in destinations
    assert extract.LEGACY_CONTRACT_DESTINATION in destinations
    assert Path(sys.prefix).resolve() not in sources
    assert Path("/usr") not in sources
    assert Path.home() not in sources
    assert extract.CHILD_PATH not in sources
    assert extract.PDF_CHILD_PATH not in sources
    assert extract.OOXML_CHILD_PATH not in sources
    assert all(binding.source.exists() for binding in bindings)


def test_antiword_runtime_detects_identity_change_after_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = extract._runtime_identity
    observations = 0

    def changing(path: Path):
        nonlocal observations
        identity = original(path)
        if path == extract.ANTIWORD_HOST_PATH:
            observations += 1
            if observations > 1:
                return (*identity[:3], identity[3] + 1)
        return identity

    monkeypatch.setattr(extract, "_runtime_identity", changing)
    with pytest.raises(RuntimeError, match="changed during discovery"):
        extract.antiword_runtime_binds()


def test_live_sandbox_extracts_public_generated_doc(tmp_path: Path) -> None:
    soffice = shutil.which("soffice")
    if soffice is None:
        pytest.skip("LibreOffice is unavailable for public DOC fixture generation")
    try:
        extract._verify_antiword_package()
    except extract.OptionalDependencyUnavailable as exc:
        pytest.skip(f"exact Antiword dependency unavailable: {exc.detail}")

    source = tmp_path / "public.html"
    output = tmp_path / "out"
    profile = tmp_path / "lo-profile"
    output.mkdir()
    profile.mkdir()
    source.write_text(
        "<html><head><meta charset='utf-8'></head><body>"
        "<p>Public Analyst legacy DOC fixture — résumé Ω "
        + chr(0x1F600)
        + "</p>"
        + "<p>Reserved example: 192.0.2.10</p>" * 20
        + "</body></html>",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            soffice, "--headless", "--nologo", "--nodefault", "--nolockcheck",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--convert-to", "doc:MS Word 97", "--outdir", str(output),
            str(source),
        ],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
        shell=False,
        env={
            "HOME": str(tmp_path), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        },
    )
    document = output / "public.doc"
    if completed.returncode != 0 or not document.is_file():
        pytest.skip(
            "LibreOffice could not generate the isolated public DOC fixture: "
            + completed.stderr.decode("utf-8", "replace")[:200]
        )

    result = _extract_path(document)
    assert result.ok, (result.reason, result.detail)
    assert result.format_name == "doc" and result.encoding == "utf-8"
    assert result.parser_version == ANTIWORD_VERSION
    assert result.package_revision == ANTIWORD_PACKAGE_REVISION
    assert result.text is not None
    assert "Public Analyst legacy DOC fixture" in result.text
    assert "résumé Ω " + chr(0x1F600) in result.text
    assert "192.0.2.10" in result.text
    assert result.logical_unit_count == len(result.legacy_units) > 0
    assert all(unit.label.startswith("output-line-") for unit in result.legacy_units)


class _FakeStream:
    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor
        self.closed = False

    def fileno(self) -> int:
        return self.descriptor

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(self, stdout: _FakeStream, stderr: _FakeStream) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.killed = False
        self.waited = False

    def poll(self):
        return None if not self.killed and not self.waited else 0

    def kill(self) -> None:
        self.killed = True

    def wait(self) -> int:
        self.waited = True
        return 0


class _FakeSelector:
    def __init__(self) -> None:
        self.entries: dict[int, SimpleNamespace] = {}

    def register(self, stream: _FakeStream, _event: object, name: str) -> None:
        self.entries[stream.fileno()] = SimpleNamespace(fileobj=stream, data=name)

    def get_map(self) -> dict[int, SimpleNamespace]:
        return self.entries

    def select(self):
        return [(value, 1) for value in tuple(self.entries.values())]

    def unregister(self, stream: _FakeStream) -> None:
        self.entries.pop(stream.fileno())

    def close(self) -> None:
        self.entries.clear()
