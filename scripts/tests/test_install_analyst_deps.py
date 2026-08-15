"""Offline guardrails for the controlled Analyst dependency installer."""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import install_analyst_deps as installer


def _archive(path: Path, name: str, body: bytes = b"ok") -> None:
    with tarfile.open(path, "w:gz") as bundle:
        info = tarfile.TarInfo(name)
        info.size = len(body)
        bundle.addfile(info, io.BytesIO(body))


def test_all_downloads_are_exact_https_artifacts() -> None:
    artifacts = (
        installer.PYMUPDF_SOURCE, installer.MUPDF_SOURCE,
        installer.DEFUSEDXML_WHEEL,
        *installer.BUILD_WHEELS,
    )
    assert len({item.filename for item in artifacts}) == len(artifacts)
    for artifact in artifacts:
        assert artifact.url.startswith("https://")
        assert len(artifact.sha256) == 64
        int(artifact.sha256, 16)
    assert installer.MUPDF_SOURCE.sha256 == \
        "21c7f064903154f1c3a7458bee81f130fc36f9b5147ea13328f9980e02d2dea2"
    assert installer.DEFUSEDXML_WHEEL.sha256 == \
        "a352e7e428770286cc899e2542b6cdaedb2b4953ff269a210103ec58f6198a61"


def test_defusedxml_notice_keeps_the_exact_upstream_license() -> None:
    license_path = Path(__file__).resolve().parents[2] / \
        "licenses/defusedxml-PSF-2.0.txt"
    assert hashlib.sha256(license_path.read_bytes()).hexdigest() == \
        "b80ce9da8c42a1f91079627fbbe2bf27210ae108a0ffe5f077d5b08e076c24c8"


def test_download_rejects_digest_mismatch_and_removes_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = io.BytesIO(b"wrong")
    response.geturl = lambda: "https://files.pythonhosted.org/file"  # type: ignore[attr-defined]
    response.__enter__ = lambda: response  # type: ignore[attr-defined]
    response.__exit__ = lambda *_args: None  # type: ignore[attr-defined]
    monkeypatch.setattr(installer.urllib.request, "urlopen", lambda *_a, **_k: response)
    artifact = installer.Artifact(
        "test.bin", "https://files.pythonhosted.org/test.bin", "0" * 64
    )
    destination = tmp_path / artifact.filename
    with pytest.raises(installer.InstallError, match="digest mismatch"):
        installer._download(artifact, destination)
    assert not destination.exists()


def test_safe_extract_accepts_exact_root_and_rejects_traversal(tmp_path: Path) -> None:
    good = tmp_path / "good.tar.gz"
    _archive(good, "mupdf-1.28.0-source/README", b"public")
    root = installer._safe_extract_mupdf(good, tmp_path / "good-out")
    assert (root / "README").read_bytes() == b"public"

    bad = tmp_path / "bad.tar.gz"
    _archive(bad, "mupdf-1.28.0-source/../escape", b"bad")
    with pytest.raises(installer.InstallError, match="path is unsafe"):
        installer._safe_extract_mupdf(bad, tmp_path / "bad-out")


def test_build_environment_is_offline_and_disables_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(installer, "_supported_host", lambda: True)
    monkeypatch.setenv("PIP_TARGET", "/poisoned-target")
    monkeypatch.setenv("PIP_CONSTRAINT", "/poisoned-constraint")
    monkeypatch.setenv("PYMUPDF_SETUP_MUPDF_BUILD_TYPE", "debug")
    monkeypatch.setenv("CC", "/poisoned-compiler")
    monkeypatch.setenv("CFLAGS", "-DPOISONED")
    monkeypatch.setenv("PYTHONPATH", "/poisoned-pythonpath")
    monkeypatch.setenv("PYTHONHOME", "/poisoned-pythonhome")

    def download(artifact, destination):
        if artifact is installer.MUPDF_SOURCE:
            _archive(
                destination,
                "mupdf-1.28.0-source/include/mupdf/fitz/version.h",
            )
        else:
            destination.write_bytes(artifact.filename.encode("ascii"))
            destination.chmod(0o600)

    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def run(command, *, environment=None):
        calls.append((command, environment))
        if "wheel" in command:
            output = Path(command[command.index("--wheel-dir") + 1])
            (output / "pymupdf-1.28.0-cp310-abi3-linux_x86_64.whl").write_bytes(b"wheel")

    monkeypatch.setattr(installer, "_download", download)
    monkeypatch.setattr(installer, "_run", run)
    monkeypatch.setattr(installer, "_verify_installed", lambda: None)
    installer.install()
    assert len(calls) == 2
    build_env = calls[0][1]
    assert build_env is not None
    assert build_env["PIP_NO_INDEX"] == "1"
    assert build_env["PIP_CONFIG_FILE"] == os.devnull
    assert build_env["PYMUPDF_SETUP_FLAVOUR"] == "pb"
    assert build_env["PYMUPDF_SETUP_MUPDF_TESSERACT"] == "0"
    assert set(build_env) == {
        "HOME", "LANG", "LC_ALL", "PATH", "PIP_CONFIG_FILE",
        "PIP_DISABLE_PIP_VERSION_CHECK", "PIP_FIND_LINKS", "PIP_NO_INDEX",
        "PYTHONNOUSERSITE", "PYMUPDF_SETUP_FLAVOUR",
        "PYMUPDF_SETUP_MUPDF_BUILD", "PYMUPDF_SETUP_MUPDF_TESSERACT",
        "TMPDIR",
    }
    assert build_env["PATH"] == "/usr/bin:/bin"
    assert Path(build_env["HOME"]).name == "home"
    assert Path(build_env["TMPDIR"]).name == "tmp"
    assert Path(build_env["PYMUPDF_SETUP_MUPDF_BUILD"]).name == \
        "mupdf-1.28.0-source"
    assert calls[1][0][3:6] == ["install", "--force-reinstall", "--no-deps"]
    assert calls[1][0][-1].endswith(installer.DEFUSEDXML_WHEEL.filename)


def test_check_reports_current_exact_versions(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(installer, "_verify_installed", lambda: None)
    assert installer.main(["--check"]) == 0
    output = capsys.readouterr().out
    assert "PyMuPDF 1.28.0, MuPDF 1.28.0" in output
    assert "defusedxml 0.7.1" in output
