from shared.smb_adapter import SMBAdapter


def test_probe_authentication_prefers_smbprotocol(monkeypatch):
    adapter = SMBAdapter(timeout_seconds=7)
    impacket_calls = {"count": 0}

    def _fake_smbprotocol(**_kwargs):
        return True, None, None

    def _fake_impacket(**_kwargs):
        impacket_calls["count"] += 1
        return False, "should not be called", "NT_STATUS_LOGON_FAILURE"

    monkeypatch.setattr(adapter, "_try_smbprotocol_auth", _fake_smbprotocol)
    monkeypatch.setattr(adapter, "_try_impacket_auth", _fake_impacket)

    result = adapter.probe_authentication("10.0.0.5", cautious_mode=False)

    assert result["success"] is True
    assert result["backend"] == "smbprotocol"
    assert result["auth_method"] == "Anonymous"
    assert impacket_calls["count"] == 0


def test_probe_authentication_falls_back_to_impacket_in_legacy(monkeypatch):
    adapter = SMBAdapter(timeout_seconds=7)

    def _fake_smbprotocol(**_kwargs):
        return False, "smbprotocol failed", "NT_STATUS_LOGON_FAILURE"

    def _fake_impacket(**kwargs):
        if kwargs.get("username") == "guest" and kwargs.get("password") == "":
            return True, None, None
        return False, "impacket failed", "NT_STATUS_LOGON_FAILURE"

    monkeypatch.setattr(adapter, "_try_smbprotocol_auth", _fake_smbprotocol)
    monkeypatch.setattr(adapter, "_try_impacket_auth", _fake_impacket)

    result = adapter.probe_authentication("10.0.0.8", cautious_mode=False)

    assert result["success"] is True
    assert result["backend"] == "impacket"
    assert result["auth_method"] == "Guest/Blank"
    assert len(result["attempts"]) >= 4


def test_probe_authentication_does_not_use_impacket_in_cautious(monkeypatch):
    adapter = SMBAdapter(timeout_seconds=7)
    impacket_calls = {"count": 0}

    def _fake_smbprotocol(**_kwargs):
        return False, "failed", "NT_STATUS_LOGON_FAILURE"

    def _fake_impacket(**_kwargs):
        impacket_calls["count"] += 1
        return True, None, None

    monkeypatch.setattr(adapter, "_try_smbprotocol_auth", _fake_smbprotocol)
    monkeypatch.setattr(adapter, "_try_impacket_auth", _fake_impacket)

    result = adapter.probe_authentication("10.0.0.9", cautious_mode=True)

    assert result["success"] is False
    assert result["backend"] is None
    assert impacket_calls["count"] == 0


def test_list_shares_normalizes_response(monkeypatch):
    adapter = SMBAdapter()

    def _fake_query(**_kwargs):
        return [
            {"shi1_netname": "Public\x00", "shi1_type": 0, "shi1_remark": "Main\x00"},
            {"shi1_netname": "IPC$\x00", "shi1_type": 3, "shi1_remark": "IPC\x00"},
        ]

    monkeypatch.setattr(adapter, "_query_shares_impacket", _fake_query)

    result = adapter.list_shares(
        "10.0.0.10",
        username="guest",
        password="",
        cautious_mode=False,
    )

    assert result["success"] is True
    assert result["shares"][0]["name"] == "Public"
    assert result["shares"][0]["is_disk"] is True
    assert result["shares"][0]["is_admin"] is False
    assert result["shares"][1]["name"] == "IPC$"
    assert result["shares"][1]["is_admin"] is True


def test_probe_share_read_marks_empty_share_inaccessible(monkeypatch):
    adapter = SMBAdapter()

    def _fake_query(**_kwargs):
        return [".", ".."]

    monkeypatch.setattr(adapter, "_query_share_entries_impacket", _fake_query)

    result = adapter.probe_share_read(
        "10.0.0.11",
        share_name="Public",
        username="guest",
        password="",
        cautious_mode=False,
    )

    assert result["accessible"] is False
    assert result["status_code"] == "ACCESS_DENIED"


def test_probe_share_read_marks_nonempty_share_accessible(monkeypatch):
    adapter = SMBAdapter()

    def _fake_query(**_kwargs):
        return [".", "..", "readme.txt"]

    monkeypatch.setattr(adapter, "_query_share_entries_impacket", _fake_query)

    result = adapter.probe_share_read(
        "10.0.0.12",
        share_name="Public",
        username="guest",
        password="",
        cautious_mode=False,
    )

    assert result["accessible"] is True
    assert result["status_code"] == "OK"
    assert result["entry_count"] == 1


def test_probe_share_read_normalizes_status_on_error(monkeypatch):
    adapter = SMBAdapter()

    def _fake_query(**_kwargs):
        raise RuntimeError("Server returned STATUS_BAD_NETWORK_NAME while listing path")

    monkeypatch.setattr(adapter, "_query_share_entries_impacket", _fake_query)

    result = adapter.probe_share_read(
        "10.0.0.13",
        share_name="Missing",
        username="guest",
        password="",
        cautious_mode=False,
    )

    assert result["accessible"] is False
    assert result["status_code"] == "NT_STATUS_BAD_NETWORK_NAME"
    assert "Share not found" in result["error"]


def test_probe_share_read_infers_timeout_from_generic_error(monkeypatch):
    adapter = SMBAdapter()

    def _fake_query(**_kwargs):
        raise RuntimeError("socket timed out while listing root")

    monkeypatch.setattr(adapter, "_query_share_entries_impacket", _fake_query)

    result = adapter.probe_share_read(
        "10.0.0.14",
        share_name="Public",
        username="guest",
        password="",
        cautious_mode=False,
    )

    assert result["accessible"] is False
    assert result["status_code"] == "TIMEOUT"
    assert "timed out" in result["error"].lower()


def test_extract_status_code_handles_nt_and_plain_status():
    adapter = SMBAdapter()
    assert adapter._extract_status_code("NT_STATUS_LOGON_FAILURE") == "NT_STATUS_LOGON_FAILURE"
    assert adapter._extract_status_code("status_access_denied") == "NT_STATUS_ACCESS_DENIED"
