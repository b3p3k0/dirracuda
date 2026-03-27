from __future__ import annotations

from shared.database import SMBSeekWorkflowDatabase


class _ConfigStub:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self.config = {}

    def get_database_path(self) -> str:
        return self._db_path


def test_get_authenticated_hosts_includes_hosts_with_denied_share_history(tmp_path) -> None:
    db = SMBSeekWorkflowDatabase(_ConfigStub(str(tmp_path / "auth_hosts_denied.db")))
    try:
        session_id = db.create_session("dirracuda", scan_type="smbseek_unified")
        server_id = db.dal.get_or_create_server(
            ip_address="10.10.10.10",
            country="US",
            auth_method="Guest/Blank",
            country_code="US",
        )
        db.dal.add_share_access(
            server_id=server_id,
            session_id=session_id,
            share_name="Public",
            accessible=False,
            error_message="Access denied",
            auth_status="NT_STATUS_ACCESS_DENIED",
        )

        hosts = db.get_authenticated_hosts(ip_filter=["10.10.10.10"])

        assert len(hosts) == 1
        assert hosts[0]["ip_address"] == "10.10.10.10"
        assert hosts[0]["auth_method"] == "Guest/Blank"
        # Denied-only history should not exclude the host from re-testing.
        assert hosts[0]["accessible_shares"] == []
    finally:
        db.close()


def test_get_or_create_server_refreshes_auth_method_for_existing_row(tmp_path) -> None:
    db = SMBSeekWorkflowDatabase(_ConfigStub(str(tmp_path / "auth_hosts_refresh.db")))
    try:
        ip = "10.20.30.40"

        db.dal.get_or_create_server(ip_address=ip, country="US", auth_method=None, country_code="US")
        before = db.db_manager.execute_query(
            "SELECT auth_method FROM smb_servers WHERE ip_address = ?",
            (ip,),
        )[0]
        assert before["auth_method"] is None

        db.dal.get_or_create_server(
            ip_address=ip,
            country="US",
            auth_method="Guest/Guest",
            country_code="US",
        )

        after = db.db_manager.execute_query(
            "SELECT auth_method FROM smb_servers WHERE ip_address = ?",
            (ip,),
        )[0]
        assert after["auth_method"] == "Guest/Guest"

        hosts = db.get_authenticated_hosts(ip_filter=[ip])
        assert len(hosts) == 1
        assert hosts[0]["auth_method"] == "Guest/Guest"
    finally:
        db.close()
