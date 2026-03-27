from pathlib import Path

import pytest

from signatures.rce_smb.loader import (
    DEFAULT_SIGNATURES_DIR,
    SignatureLoadError,
    SignatureLoader,
)


def test_signature_loader_defaults_to_conf_signatures_dir():
    loader = SignatureLoader()
    assert loader.signatures_dir == DEFAULT_SIGNATURES_DIR
    assert loader.signatures_dir == Path("conf/signatures/rce_smb").resolve()


def test_signature_loader_load_all_succeeds_from_default_conf_path():
    loader = SignatureLoader()
    signatures = loader.load_all()
    assert len(signatures) > 0
    assert all(sig.source_file.endswith(".yaml") for sig in signatures)


def test_signature_loader_missing_dir_error_mentions_conf_target(tmp_path):
    missing_dir = tmp_path / "missing"
    loader = SignatureLoader(signatures_dir=str(missing_dir))

    with pytest.raises(SignatureLoadError) as exc_info:
        loader.discover_signature_files()

    message = str(exc_info.value)
    assert "Signatures directory not found" in message
    assert "conf/signatures/rce_smb" in message
