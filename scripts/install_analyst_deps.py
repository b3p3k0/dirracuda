#!/usr/bin/env python3
"""Install the exact optional Analyst document-parser dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

PYMUPDF_VERSION: Final = "1.28.0"
MUPDF_VERSION: Final = "1.28.0"
DEFUSEDXML_VERSION: Final = "0.7.1"
MAX_DOWNLOAD_BYTES: Final = 256 * 1024 * 1024
READ_SIZE: Final = 1024 * 1024


@dataclass(frozen=True, slots=True)
class Artifact:
    filename: str
    url: str
    sha256: str


PYMUPDF_SOURCE: Final = Artifact(
    "pymupdf-1.28.0.tar.gz",
    "https://files.pythonhosted.org/packages/8e/e9/6d6c5d6c0a3551bffd47681a6240caf941727f195b45593cf20ab36f018f/pymupdf-1.28.0.tar.gz",
    "e53f3567403a92da15caa9e7ae0164327fff48817e9f40175367fb9de524258d",
)
MUPDF_SOURCE: Final = Artifact(
    "mupdf-1.28.0-source.tar.gz",
    "https://mupdf.com/downloads/archive/mupdf-1.28.0-source.tar.gz",
    "21c7f064903154f1c3a7458bee81f130fc36f9b5147ea13328f9980e02d2dea2",
)
DEFUSEDXML_WHEEL: Final = Artifact(
    "defusedxml-0.7.1-py2.py3-none-any.whl",
    "https://files.pythonhosted.org/packages/07/6c/aa3f2f849e01cb6a001cd8554a88d4c77c5c1a31c95bdf1cf9301e6d9ef4/defusedxml-0.7.1-py2.py3-none-any.whl",
    "a352e7e428770286cc899e2542b6cdaedb2b4953ff269a210103ec58f6198a61",
)
BUILD_WHEELS: Final = (
    Artifact(
        "pipcl-12-py3-none-any.whl",
        "https://files.pythonhosted.org/packages/eb/61/4de83121d7dd79fac47b13d781b8041314176564ba76f9c5a7cdca5376bf/pipcl-12-py3-none-any.whl",
        "aa34a85e10701758871f439303f6aace94b18ef9cd6bef69d5948b7afc0e125b",
    ),
    Artifact(
        "packaging-26.3-py3-none-any.whl",
        "https://files.pythonhosted.org/packages/63/34/ba1c580383c9eada3711951fef0795c80b829a078d72188184bcab9dd527/packaging-26.3-py3-none-any.whl",
        "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c",
    ),
    Artifact(
        "libclang-18.1.1-py2.py3-none-manylinux2010_x86_64.whl",
        "https://files.pythonhosted.org/packages/1d/fc/716c1e62e512ef1c160e7984a73a5fc7df45166f2ff3f254e71c58076f7c/libclang-18.1.1-py2.py3-none-manylinux2010_x86_64.whl",
        "c533091d8a3bbf7460a00cb6c1a71da93bffe148f172c7d03b1c31fbf8aa2a0b",
    ),
    Artifact(
        "swig-4.4.1-py3-none-manylinux_2_12_x86_64.manylinux2010_x86_64.whl",
        "https://files.pythonhosted.org/packages/fa/7b/e3a14d053fa18b0d2e14efcc21883816964ddbe52a0c43018e195a99aba2/swig-4.4.1-py3-none-manylinux_2_12_x86_64.manylinux2010_x86_64.whl",
        "ae3da2bf679a4c942a2c100789395d4d167e7da8286018124e4665f5eff43e31",
    ),
)
ALLOWED_DOWNLOAD_HOSTS: Final = {
    "files.pythonhosted.org",
    "mupdf.com",
    "release-assets.githubusercontent.com",
}


class InstallError(RuntimeError):
    """A content-free, actionable installer failure."""


def _supported_host() -> bool:
    return (
        sys.platform.startswith("linux")
        and platform.machine().lower() in {"x86_64", "amd64"}
        and sys.version_info >= (3, 10)
    )


def _download(artifact: Artifact, destination: Path) -> None:
    parsed = urllib.parse.urlsplit(artifact.url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise InstallError("dependency URL is outside the frozen HTTPS allowlist")
    request = urllib.request.Request(
        artifact.url, headers={"User-Agent": "Dirracuda-Analyst-Installer/1"}
    )
    digest = hashlib.sha256()
    total = 0
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            final = urllib.parse.urlsplit(response.geturl())
            if final.scheme != "https" or final.hostname not in ALLOWED_DOWNLOAD_HOSTS:
                raise InstallError("dependency redirect left the HTTPS allowlist")
            with os.fdopen(os.open(destination, flags, 0o600), "wb") as output:
                while True:
                    chunk = response.read(READ_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise InstallError("dependency download exceeded its byte cap")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
    except InstallError:
        destination.unlink(missing_ok=True)
        raise
    except (OSError, ValueError) as exc:
        destination.unlink(missing_ok=True)
        raise InstallError("dependency download failed") from exc
    if digest.hexdigest() != artifact.sha256:
        destination.unlink(missing_ok=True)
        raise InstallError("dependency digest mismatch")


def _safe_extract_mupdf(archive: Path, destination: Path) -> Path:
    expected_root = PurePosixPath(f"mupdf-{MUPDF_VERSION}-source")
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            link_paths: set[PurePosixPath] = set()
            for member in members:
                name = PurePosixPath(member.name)
                if (name.is_absolute() or not name.parts
                        or name.parts[0] != expected_root.name
                        or any(part in {"", ".", ".."} for part in name.parts)):
                    raise InstallError("MuPDF archive path is unsafe")
                if not (member.isfile() or member.isdir()
                        or member.issym() or member.islnk()):
                    raise InstallError("MuPDF archive entry type is unsafe")
                if member.issym() or member.islnk():
                    link = PurePosixPath(member.linkname)
                    base = name.parent if member.issym() else PurePosixPath()
                    combined = _normalize_posix(base, link)
                    if not combined.parts or combined.parts[0] != expected_root.name:
                        raise InstallError("MuPDF archive link escapes its root")
                    link_paths.add(name)
            for member in members:
                name = PurePosixPath(member.name)
                if any(parent in link_paths for parent in name.parents):
                    raise InstallError("MuPDF archive writes through a link")
            if hasattr(tarfile, "data_filter"):
                bundle.extractall(destination, members=members, filter="data")
            else:  # Python 3.10/3.11: exact archive hash + checks above own safety.
                bundle.extractall(destination, members=members)
    except (OSError, tarfile.TarError) as exc:
        raise InstallError("MuPDF archive extraction failed") from exc
    root = destination / expected_root.name
    observed = root.stat()
    if not stat.S_ISDIR(observed.st_mode):
        raise InstallError("MuPDF archive root is absent")
    return root


def _normalize_posix(base: PurePosixPath, link: PurePosixPath) -> PurePosixPath:
    if link.is_absolute():
        return PurePosixPath("/")
    parts: list[str] = []
    for part in (*base.parts, *link.parts):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return PurePosixPath("/")
            parts.pop()
        else:
            parts.append(part)
    return PurePosixPath(*parts)


def _run(command: list[str], *, environment: dict[str, str] | None = None) -> None:
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            timeout=3600,
            check=False,
            shell=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallError("dependency build command failed to execute") from exc
    if completed.returncode != 0:
        raise InstallError("dependency build command failed")


def _verify_installed() -> None:
    probe = (
        "import json,pymupdf; from importlib import metadata; print(json.dumps({"
        "'defusedxml':metadata.version('defusedxml'),"
        "'pymupdf':pymupdf.pymupdf_version,"
        "'mupdf':pymupdf.mupdf_version},sort_keys=True))"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", probe],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
            shell=False,
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        )
        result = json.loads(completed.stdout.decode("ascii", errors="strict"))
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError) as exc:
        raise InstallError("installed dependency verification failed") from exc
    if completed.returncode != 0 or result != {
        "defusedxml": DEFUSEDXML_VERSION,
        "mupdf": MUPDF_VERSION,
        "pymupdf": PYMUPDF_VERSION,
    }:
        raise InstallError("installed dependency versions do not match the lock")


def install() -> None:
    if not _supported_host():
        raise InstallError(
            "controlled Analyst build supports Linux x86_64 with Python 3.10+"
        )
    with tempfile.TemporaryDirectory(prefix="dirracuda-analyst-deps-") as raw:
        scratch = Path(raw)
        source_dir = scratch / "source"
        wheelhouse = scratch / "wheelhouse"
        output_dir = scratch / "output"
        for directory in (source_dir, wheelhouse, output_dir):
            directory.mkdir(mode=0o700)
        pymupdf_source = source_dir / PYMUPDF_SOURCE.filename
        mupdf_source = source_dir / MUPDF_SOURCE.filename
        _download(PYMUPDF_SOURCE, pymupdf_source)
        _download(MUPDF_SOURCE, mupdf_source)
        for artifact in BUILD_WHEELS:
            _download(artifact, wheelhouse / artifact.filename)
        _download(DEFUSEDXML_WHEEL, wheelhouse / DEFUSEDXML_WHEEL.filename)
        mupdf_root = _safe_extract_mupdf(mupdf_source, source_dir)

        build_home = scratch / "home"
        build_tmp = scratch / "tmp"
        build_home.mkdir(mode=0o700)
        build_tmp.mkdir(mode=0o700)
        environment = {
            "HOME": str(build_home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_FIND_LINKS": str(wheelhouse),
            "PIP_NO_INDEX": "1",
            "PYTHONNOUSERSITE": "1",
            "PYMUPDF_SETUP_FLAVOUR": "pb",
            "PYMUPDF_SETUP_MUPDF_BUILD": str(mupdf_root),
            "PYMUPDF_SETUP_MUPDF_TESSERACT": "0",
            "TMPDIR": str(build_tmp),
        }
        _run([
            sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-cache-dir",
            "--wheel-dir", str(output_dir), str(pymupdf_source),
        ], environment=environment)
        wheels = tuple(output_dir.glob("pymupdf-1.28.0-*.whl"))
        if len(wheels) != 1:
            raise InstallError("build did not produce exactly one PyMuPDF wheel")
        _run([
            sys.executable, "-m", "pip", "install", "--force-reinstall",
            "--no-deps", "--no-index", str(wheels[0]),
            str(wheelhouse / DEFUSEDXML_WHEEL.filename),
        ], environment=environment)
    _verify_installed()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install the exact Analyst parser dependencies."
    )
    parser.add_argument(
        "--check", action="store_true",
        help="verify all installed Analyst parser dependency versions",
    )
    args = parser.parse_args(argv)
    try:
        if args.check:
            _verify_installed()
        else:
            install()
    except InstallError as exc:
        print(f"Analyst dependency install failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Analyst parser dependencies PASS: PyMuPDF {PYMUPDF_VERSION}, "
        f"MuPDF {MUPDF_VERSION}, defusedxml {DEFUSEDXML_VERSION}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
