from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import AuditLog, Device, Event, Setting, utcnow
from app.security.service import invalidate_security_cache
from app.services.context import system_context
from app.services.devices import DeviceService, UnsupportedDeviceActionError
from app.unifi.base import UniFiAdapter
from app.unifi.factory import close_unifi_adapter, get_adapter_info
from app.unifi.legacy import LegacyUniFiAdapter
from app.unifi.official import OfficialUniFiAdapter
from app.unifi.status import (
    UniFiConnectionStatus,
    get_unifi_connection_status,
    sanitize_unifi_error,
)
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests.conftest import basic_auth_header, bootstrap_security_for_tests

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)
AUTH = basic_auth_header()


@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    close_unifi_adapter()
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr("app.db.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.main.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.security.middleware.SessionLocal", TestingSessionLocal)
    invalidate_security_cache()
    app.dependency_overrides[get_db] = override_get_db

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    bootstrap_security_for_tests(db)
    db.close()
    yield
    close_unifi_adapter()


# ==============================================================================
# 1. Error Sanitization Tests
# ==============================================================================


def test_sanitize_unifi_error_none_and_empty():
    assert sanitize_unifi_error(None) is None
    assert sanitize_unifi_error("") is None


def test_sanitize_unifi_error_redacts_api_key(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("super-secret-key-12345"))
    raw = "Failed communicating with key super-secret-key-12345 on controller"
    cleaned = sanitize_unifi_error(raw)
    assert "super-secret-key-12345" not in cleaned
    assert "***" in cleaned


def test_sanitize_unifi_error_redacts_password(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_PASSWORD", SecretStr("myUltraSecretPassword99!"))
    raw = "Failed authenticating as admin with password myUltraSecretPassword99!"
    cleaned = sanitize_unifi_error(raw)
    assert "myUltraSecretPassword99!" not in cleaned
    assert "***" in cleaned


def test_sanitize_unifi_error_redacts_app_secret_key(monkeypatch):
    monkeypatch.setattr(settings, "APP_SECRET_KEY", SecretStr("myAppSecretKey4567!"))
    raw = "Encrypted token error using secret key myAppSecretKey4567! during operation"
    cleaned = sanitize_unifi_error(raw)
    assert "myAppSecretKey4567!" not in cleaned
    assert "***" in cleaned


def test_sanitize_unifi_error_redacts_query_tokens_and_headers():
    raw_query = "Error requesting https://unifi.local/api?token=sensitiveTokenValue123&site=default"
    cleaned_query = sanitize_unifi_error(raw_query)
    assert "sensitiveTokenValue123" not in cleaned_query
    assert "token=***" in cleaned_query

    raw_header = "Failed header validation X-API-KEY: rawSecretApiKeyVal789 on host"
    cleaned_header = sanitize_unifi_error(raw_header)
    assert "rawSecretApiKeyVal789" not in cleaned_header
    assert "X-API-KEY: ***" in cleaned_header


def test_sanitize_unifi_error_redacts_url_credentials():
    raw = "Failed connecting to https://admin:secretPass@192.168.1.1:8443/api"
    cleaned = sanitize_unifi_error(raw)
    assert "secretPass" not in cleaned
    assert "https://admin:***@192.168.1.1:8443/api" in cleaned


def test_sanitize_unifi_error_condenses_traceback():
    raw = (
        "Traceback (most recent call last):\n"
        '  File "app/unifi/official.py", line 42, in probe\n'
        "  httpx.ConnectError: [Errno 61] Connection refused"
    )
    cleaned = sanitize_unifi_error(raw)
    assert "Traceback" not in cleaned
    assert "httpx.ConnectError: [Errno 61] Connection refused" in cleaned


def test_sanitize_unifi_error_truncates_long_messages():
    long_msg = "A" * 400
    cleaned = sanitize_unifi_error(long_msg)
    assert len(cleaned) <= 250
    assert cleaned.endswith("...")


# ==============================================================================
# 2. Status Model and Inspection Tests
# ==============================================================================


def test_get_adapter_info_uninitialized(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_API_MODE", "official")
    mode, adapter, caps = get_adapter_info()
    assert mode == "official"
    assert adapter == "uninitialized"
    assert caps.supports_pagination is False
    assert caps.can_block is False

    monkeypatch.setattr(settings, "UNIFI_API_MODE", "legacy")
    mode, adapter, caps = get_adapter_info()
    assert mode == "legacy"
    assert adapter == "uninitialized"
    assert caps.can_block is False
    assert caps.supports_pagination is False

    monkeypatch.setattr(settings, "UNIFI_API_MODE", "auto")
    mode, adapter, caps = get_adapter_info()
    assert mode == "auto"
    assert adapter == "uninitialized"
    assert caps.can_block is False


def test_get_adapter_info_with_initialized_adapter(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_API_MODE", "auto")
    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=OfficialUniFiAdapter)
    mock_adapter.capabilities = OfficialUniFiAdapter.capabilities
    factory._shared_adapter = mock_adapter

    mode, adapter, caps = get_adapter_info()
    assert mode == "auto"
    assert adapter == "official"
    assert caps.supports_pagination is True

    mock_legacy = MagicMock(spec=LegacyUniFiAdapter)
    mock_legacy.capabilities = LegacyUniFiAdapter.capabilities
    factory._shared_adapter = mock_legacy

    mode, adapter, caps = get_adapter_info()
    assert mode == "auto"
    assert adapter == "legacy"
    assert caps.can_block is True


def test_get_unifi_connection_status_no_db():
    status = get_unifi_connection_status(db=None)
    assert isinstance(status, UniFiConnectionStatus)
    assert status.connection_state == "unknown"
    assert status.last_scan_at is None
    assert status.last_scan_result is None
    assert status.last_successful_scan_at is None


def test_get_unifi_connection_status_empty_db():
    db = TestingSessionLocal()
    try:
        status = get_unifi_connection_status(db)
        assert status.connection_state == "unknown"
        assert status.last_scan_at is None
        assert status.last_successful_scan_at is None
        assert status.last_error is None
    finally:
        db.close()


def test_get_unifi_connection_status_successful_scan():
    db = TestingSessionLocal()
    try:
        now = utcnow()
        db.add(
            Event(
                event_type="scan_finished",
                severity="info",
                message="Scan finished (scheduled). Processed 5 clients.",
                created_at=now,
            )
        )
        db.commit()

        status = get_unifi_connection_status(db)
        assert status.connection_state == "successful"
        assert status.last_scan_result is True
        assert status.last_scan_at.replace(tzinfo=UTC) == now
        assert status.last_successful_scan_at.replace(tzinfo=UTC) == now
        assert status.last_error is None
    finally:
        db.close()


def test_get_unifi_connection_status_stale_scan():
    db = TestingSessionLocal()
    try:
        # Default scan interval is 300 seconds. Scan from 15 minutes ago is stale.
        stale_time = utcnow() - timedelta(minutes=15)
        db.add(
            Event(
                event_type="scan_finished",
                severity="info",
                message="Scan finished. Processed 5 clients.",
                created_at=stale_time,
            )
        )
        db.commit()

        status = get_unifi_connection_status(db)
        assert status.connection_state == "degraded"
        assert status.last_scan_result is True
        assert status.last_scan_at.replace(tzinfo=UTC) == stale_time
        assert status.last_successful_scan_at.replace(tzinfo=UTC) == stale_time
    finally:
        db.close()


def test_get_unifi_connection_status_stale_scan_db_override():
    db = TestingSessionLocal()
    try:
        # Override scan interval to 60 seconds via Setting in DB
        db.add(Setting(key="scan_interval_seconds", value="60"))
        db.commit()

        # Threshold: max(60 * 2, 60 + 60) = 120s. A scan from 150s ago should be degraded.
        stale_time = utcnow() - timedelta(seconds=150)
        db.add(
            Event(
                event_type="scan_finished",
                severity="info",
                message="Scan finished. Processed 5 clients.",
                created_at=stale_time,
            )
        )
        db.commit()

        status = get_unifi_connection_status(db)
        assert status.connection_state == "degraded"

        # A scan from 50s ago should NOT be degraded.
        recent_time = utcnow() - timedelta(seconds=50)
        db.add(
            Event(
                event_type="scan_finished",
                severity="info",
                message="Scan finished. Processed 5 clients.",
                created_at=recent_time,
            )
        )
        db.commit()

        status = get_unifi_connection_status(db)
        assert status.connection_state == "successful"
    finally:
        db.close()


def test_get_unifi_connection_status_failed_scan_with_previous_success():
    db = TestingSessionLocal()
    try:
        t_success = datetime(2026, 11, 9, 11, 0, 0, tzinfo=UTC)
        t_failure = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)
        db.add(
            Event(
                event_type="scan_finished",
                severity="info",
                message="Scan finished. Processed 2 clients.",
                created_at=t_success,
            )
        )
        db.add(
            Event(
                event_type="scan_failed",
                severity="error",
                message="Failed to fetch clients from UniFi: Connection refused",
                created_at=t_failure,
            )
        )
        db.commit()

        status = get_unifi_connection_status(db)
        assert status.connection_state == "failed"
        assert status.last_scan_result is False
        assert status.last_scan_at.replace(tzinfo=UTC) == t_failure
        assert status.last_successful_scan_at.replace(tzinfo=UTC) == t_success
        assert "Connection refused" in status.last_error
    finally:
        db.close()


def test_get_unifi_connection_status_failed_scan_without_previous_success():
    db = TestingSessionLocal()
    try:
        t_failure = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)
        db.add(
            Event(
                event_type="scan_failed",
                severity="error",
                message="Failed connecting to controller",
                created_at=t_failure,
            )
        )
        db.commit()

        status = get_unifi_connection_status(db)
        assert status.connection_state == "failed"
        assert status.last_scan_result is False
        assert status.last_scan_at.replace(tzinfo=UTC) == t_failure
        assert status.last_successful_scan_at is None
        assert "Failed connecting" in status.last_error
    finally:
        db.close()


def test_unifi_connection_status_to_dict():
    status = UniFiConnectionStatus(
        configured_mode="auto",
        active_adapter="official",
        configured_site="default",
        connection_state="successful",
        last_scan_at=datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC),
        last_scan_result=True,
        last_successful_scan_at=datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC),
        last_error=None,
        capabilities=OfficialUniFiAdapter.capabilities,
        mock_mode=False,
        dry_run_blocks=True,
    )
    d = status.to_dict()
    assert d["configured_mode"] == "auto"
    assert d["active_adapter"] == "official"
    assert d["configured_site"] == "default"
    assert d["connection_state"] == "successful"
    assert d["capabilities"]["can_block"] is False
    assert d["capabilities"]["supports_pagination"] is True
    assert d["mock_mode"] is False
    assert d["dry_run_blocks"] is True


# ==============================================================================
# 3. Non-Blocking Dashboard Rendering Tests
# ==============================================================================


def test_dashboard_rendering_does_not_call_unifi_controller(monkeypatch):
    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=UniFiAdapter)
    mock_adapter.capabilities = LegacyUniFiAdapter.capabilities
    mock_adapter.mock_mode = False
    factory._shared_adapter = mock_adapter

    response = client.get("/", headers=AUTH)
    assert response.status_code == 200
    assert "UniFi Connection & Scanner" in response.text
    assert "Adapter Capabilities" in response.text
    assert "Trigger Manual Scan" in response.text
    assert "Test UniFi Connection" in response.text

    # Verify no controller API requests were made during rendering
    mock_adapter.get_clients.assert_not_called()
    mock_adapter.test_connection.assert_not_called()


# ==============================================================================
# 4. Status API Route Tests
# ==============================================================================


def test_get_unifi_status_requires_auth():
    response = client.get("/api/unifi/status")
    assert response.status_code in (401, 403)


def test_get_unifi_status_authenticated():
    response = client.get("/api/unifi/status", headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert "configured_mode" in data
    assert "active_adapter" in data
    assert "connection_state" in data
    assert "capabilities" in data
    assert "mock_mode" in data
    assert "dry_run_blocks" in data


# ==============================================================================
# 5. Manual Connection Test Route Tests
# ==============================================================================


def test_test_connection_requires_auth():
    response = client.post("/api/unifi/test-connection")
    assert response.status_code in (401, 403)


def test_test_connection_success_legacy(monkeypatch):
    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=LegacyUniFiAdapter)
    mock_adapter.capabilities = LegacyUniFiAdapter.capabilities
    mock_adapter.mock_mode = False
    mock_adapter.test_connection.return_value = (True, "Successfully connected to UniFi controller")
    factory._shared_adapter = mock_adapter

    response = client.post("/api/unifi/test-connection", headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "Successfully connected" in data["message"]
    assert data["adapter"] == "legacy"
    assert data["capabilities"]["can_block"] is True


def test_test_connection_success_official(monkeypatch):
    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=OfficialUniFiAdapter)
    mock_adapter.capabilities = OfficialUniFiAdapter.capabilities
    mock_adapter.mock_mode = False
    mock_adapter.test_connection.return_value = (True, "Successfully connected to official API")
    factory._shared_adapter = mock_adapter

    response = client.post("/api/unifi/test-connection", headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "Successfully connected" in data["message"]
    assert data["adapter"] == "official"
    assert data["capabilities"]["can_block"] is False


def test_test_connection_auth_error(monkeypatch):
    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=OfficialUniFiAdapter)
    mock_adapter.capabilities = OfficialUniFiAdapter.capabilities
    mock_adapter.mock_mode = False
    mock_adapter.test_connection.return_value = (
        False,
        "UniFi official API authentication failed: invalid API key (HTTP 401)",
    )
    factory._shared_adapter = mock_adapter

    response = client.post("/api/unifi/test-connection", headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "invalid API key" in data["message"]


def test_test_connection_permission_error(monkeypatch):
    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=OfficialUniFiAdapter)
    mock_adapter.capabilities = OfficialUniFiAdapter.capabilities
    mock_adapter.mock_mode = False
    mock_adapter.test_connection.return_value = (
        False,
        "UniFi official API access denied: insufficient permissions (HTTP 403)",
    )
    factory._shared_adapter = mock_adapter

    response = client.post("/api/unifi/test-connection", headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "insufficient permissions" in data["message"]


def test_test_connection_network_failure(monkeypatch):
    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=LegacyUniFiAdapter)
    mock_adapter.capabilities = LegacyUniFiAdapter.capabilities
    mock_adapter.mock_mode = False
    mock_adapter.test_connection.return_value = (
        False,
        "Failed to connect to UniFi controller: [Errno 61] Connection refused",
    )
    factory._shared_adapter = mock_adapter

    response = client.post("/api/unifi/test-connection", headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "Connection refused" in data["message"]


def test_test_connection_does_not_mutate_devices_or_events(monkeypatch):
    db = TestingSessionLocal()
    device = Device(mac="aa:bb:cc:dd:ee:ff", status="unknown")
    db.add(device)
    db.commit()
    db.close()

    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=LegacyUniFiAdapter)
    mock_adapter.capabilities = LegacyUniFiAdapter.capabilities
    mock_adapter.mock_mode = False
    mock_adapter.test_connection.return_value = (True, "Connection OK")
    factory._shared_adapter = mock_adapter

    response = client.post("/api/unifi/test-connection", headers=AUTH)
    assert response.status_code == 200

    db = TestingSessionLocal()
    d = db.query(Device).filter(Device.mac == "aa:bb:cc:dd:ee:ff").one()
    assert d.status == "unknown"
    # Ensure no scan events were recorded
    events = db.query(Event).filter(Event.event_type.in_(["scan_finished", "scan_failed"])).all()
    assert len(events) == 0
    db.close()


def test_test_connection_unexpected_crash(monkeypatch):
    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=LegacyUniFiAdapter)
    mock_adapter.test_connection.side_effect = RuntimeError("Fatal hardware socket abort")
    factory._shared_adapter = mock_adapter

    response = client.post("/api/unifi/test-connection", headers=AUTH)
    assert response.status_code == 500
    data = response.json()
    assert data["success"] is False
    assert "Fatal hardware socket abort" not in data["message"]
    assert "Unexpected error executing connection test" in data["message"]


def test_unexpected_exception_in_test_connection_redaction(monkeypatch):
    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=LegacyUniFiAdapter)
    mock_adapter.test_connection.side_effect = RuntimeError(
        "Critical DB leak postgres://superuser:secretPassWord123@internal.db:5432/main"
    )
    factory._shared_adapter = mock_adapter

    response = client.post("/api/unifi/test-connection", headers=AUTH)
    assert response.status_code == 500
    data = response.json()
    assert data["success"] is False
    # Raw internal exception details and credentials must never leak in response payload
    assert "secretPassWord123" not in response.text
    assert "postgres://" not in response.text
    assert "Critical DB leak" not in response.text
    assert (
        data["message"]
        == "Unexpected error executing connection test. Check application logs for details."
    )


@pytest.mark.asyncio
async def test_startup_resilience_auto_mode_invalid_api_key(monkeypatch):
    import app.unifi.factory as factory
    from app.main import lifespan
    from app.scanner import run_scan
    from app.unifi.models import UniFiAuthError

    monkeypatch.setattr(settings, "UNIFI_API_MODE", "auto")
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("invalid-api-key"))
    factory.close_unifi_adapter()

    # Mock probe to raise UniFiAuthError (401)
    with (
        patch("app.main.scheduler"),
        patch("app.main.scheduled_oui_update"),
        patch("app.unifi.official.OfficialUniFiAdapter.probe") as mock_probe,
    ):
        mock_probe.side_effect = UniFiAuthError(
            "UniFi official API authentication failed: invalid API key (HTTP 401)"
        )

        # Verify lifespan warmup does not raise
        async with lifespan(app):
            # Lifespan started successfully without crashing
            assert factory._shared_adapter is None

            # Dashboard endpoint remains accessible and reports Not Initialized
            resp_dash = client.get("/", headers=AUTH)
            assert resp_dash.status_code == 200
            assert "Not Initialized" in resp_dash.text

            # Status endpoint remains accessible and reports uninitialized
            resp_status = client.get("/api/unifi/status", headers=AUTH)
            assert resp_status.status_code == 200
            data = resp_status.json()
            assert data["configured_mode"] == "auto"
            assert data["active_adapter"] == "uninitialized"

            # Connection test endpoint catches UniFiAuthError and returns 200 with sanitized error
            resp_test = client.post("/api/unifi/test-connection", headers=AUTH)
            assert resp_test.status_code == 200
            data_test = resp_test.json()
            assert data_test["success"] is False
            assert "invalid API key" in data_test["message"]
            assert data_test["adapter"] == "uninitialized"

            # Background/manual scan records scan_failed event in DB
            db = TestingSessionLocal()
            try:
                scan_res = run_scan(db)
                assert scan_res["success"] is False
                assert "invalid API key" in scan_res["message"]

                failed_event = (
                    db.query(Event)
                    .filter(Event.event_type == "scan_failed")
                    .order_by(Event.id.desc())
                    .first()
                )
                assert failed_event is not None
                assert "invalid API key" in failed_event.message

                # get_unifi_connection_status now reports failed
                conn_status = get_unifi_connection_status(db)
                assert conn_status.connection_state == "failed"
                assert "invalid API key" in conn_status.last_error
            finally:
                db.close()


@pytest.mark.asyncio
async def test_startup_resilience_controller_unavailable(monkeypatch):
    import app.unifi.factory as factory
    from app.main import lifespan
    from app.unifi.models import UniFiConnectionError

    monkeypatch.setattr(settings, "UNIFI_API_MODE", "official")
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-key"))
    factory.close_unifi_adapter()

    with (
        patch("app.main.scheduler"),
        patch("app.main.scheduled_oui_update"),
        patch("app.main.get_unifi_client") as mock_warmup,
    ):
        mock_warmup.side_effect = UniFiConnectionError("Connection refused to 192.168.1.1:443")

        # Lifespan should not crash
        async with lifespan(app):
            resp_dash = client.get("/", headers=AUTH)
            assert resp_dash.status_code == 200

            resp_status = client.get("/api/unifi/status", headers=AUTH)
            assert resp_status.status_code == 200
            assert resp_status.json()["active_adapter"] == "uninitialized"


# ==============================================================================
# 6. Server-Side Capability Enforcement Tests
# ==============================================================================


def test_service_block_and_unblock_legacy_supported(monkeypatch):
    db = TestingSessionLocal()
    device = Device(mac="00:11:22:33:44:55", status="unknown")
    db.add(device)
    db.commit()

    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=LegacyUniFiAdapter)
    mock_adapter.capabilities = LegacyUniFiAdapter.capabilities
    mock_adapter.block_client.return_value = True
    mock_adapter.unblock_client.return_value = True
    factory._shared_adapter = mock_adapter

    service = DeviceService(db, system_context())

    blocked_device = service.block(device.id)
    assert blocked_device.status == "blocked"
    mock_adapter.block_client.assert_called_once_with("00:11:22:33:44:55")

    unblocked_device = service.unblock(device.id)
    assert unblocked_device.status == "unknown"
    mock_adapter.unblock_client.assert_called_once_with("00:11:22:33:44:55")
    db.close()


def test_service_block_official_rejected(monkeypatch):
    db = TestingSessionLocal()
    device = Device(mac="00:11:22:33:44:55", status="unknown")
    db.add(device)
    db.commit()

    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=OfficialUniFiAdapter)
    mock_adapter.capabilities = OfficialUniFiAdapter.capabilities
    factory._shared_adapter = mock_adapter

    service = DeviceService(db, system_context())

    with pytest.raises(UnsupportedDeviceActionError) as exc_info:
        service.block(device.id)

    assert "not supported" in str(exc_info.value)

    # Ensure device status was not altered
    db.refresh(device)
    assert device.status == "unknown"

    # Ensure no audit log was created
    audit = db.query(AuditLog).filter(AuditLog.target_id == str(device.id)).all()
    assert len(audit) == 0
    db.close()


def test_service_unblock_official_rejected(monkeypatch):
    db = TestingSessionLocal()
    device = Device(mac="00:11:22:33:44:55", status="blocked")
    db.add(device)
    db.commit()

    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=OfficialUniFiAdapter)
    mock_adapter.capabilities = OfficialUniFiAdapter.capabilities
    factory._shared_adapter = mock_adapter

    service = DeviceService(db, system_context())

    with pytest.raises(UnsupportedDeviceActionError) as exc_info:
        service.unblock(device.id)

    assert "not supported" in str(exc_info.value)

    # Ensure device status remains blocked
    db.refresh(device)
    assert device.status == "blocked"
    db.close()


def test_service_dry_run_does_not_bypass_unsupported_block(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_DRY_RUN_BLOCKS", True)
    db = TestingSessionLocal()
    device = Device(mac="00:11:22:33:44:55", status="unknown")
    db.add(device)
    db.commit()

    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=OfficialUniFiAdapter)
    mock_adapter.capabilities = OfficialUniFiAdapter.capabilities
    factory._shared_adapter = mock_adapter

    service = DeviceService(db, system_context())

    with pytest.raises(UnsupportedDeviceActionError):
        service.block(device.id)

    db.refresh(device)
    assert device.status == "unknown"
    db.close()


def test_routes_devices_block_and_unblock_rejections_409(monkeypatch):
    db = TestingSessionLocal()
    device = Device(mac="00:11:22:33:44:55", status="unknown")
    db.add(device)
    db.commit()
    device_id = device.id
    db.close()

    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=OfficialUniFiAdapter)
    mock_adapter.capabilities = OfficialUniFiAdapter.capabilities
    factory._shared_adapter = mock_adapter

    # 1. JSON endpoint block
    resp = client.post(f"/api/devices/{device_id}/block", headers=AUTH)
    assert resp.status_code == 409
    assert "not supported" in resp.json()["detail"]

    # 2. JSON endpoint unblock
    resp = client.post(f"/api/devices/{device_id}/unblock", headers=AUTH)
    assert resp.status_code == 409
    assert "not supported" in resp.json()["detail"]

    # 3. HTMX block modal
    resp = client.get(f"/api/devices/htmx/{device_id}/block_modal", headers=AUTH)
    assert resp.status_code == 409
    assert "not supported" in resp.text

    # 4. HTMX block action
    resp = client.post(f"/api/devices/htmx/{device_id}/block", headers=AUTH)
    assert resp.status_code == 409
    assert "not supported" in resp.text

    # 5. HTMX unblock action
    resp = client.post(f"/api/devices/htmx/{device_id}/unblock", headers=AUTH)
    assert resp.status_code == 409
    assert "not supported" in resp.text


def test_trust_and_ignore_work_in_official_mode(monkeypatch):
    db = TestingSessionLocal()
    device = Device(mac="00:11:22:33:44:55", status="unknown")
    db.add(device)
    db.commit()
    device_id = device.id
    db.close()

    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=OfficialUniFiAdapter)
    mock_adapter.capabilities = OfficialUniFiAdapter.capabilities
    factory._shared_adapter = mock_adapter

    # Trust device
    resp = client.post(f"/api/devices/{device_id}/trust", headers=AUTH)
    assert resp.status_code == 200

    db = TestingSessionLocal()
    d = db.query(Device).filter(Device.id == device_id).one()
    assert d.status == "trusted"
    db.close()

    # Ignore device
    resp = client.post(f"/api/devices/{device_id}/ignore", headers=AUTH)
    assert resp.status_code == 200

    db = TestingSessionLocal()
    d = db.query(Device).filter(Device.id == device_id).one()
    assert d.status == "ignored"
    db.close()


# ==============================================================================
# 7. UI Template Fragment Rendering Tests
# ==============================================================================


def test_scan_status_panel_htmx_endpoint():
    db = TestingSessionLocal()
    now = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)
    db.add(
        Event(
            event_type="scan_finished",
            severity="info",
            message="Scan finished. 3 clients.",
            created_at=now,
        )
    )
    db.commit()
    db.close()

    response = client.get("/htmx/scan-status", headers=AUTH)
    assert response.status_code == 200
    assert "scan-status-panel" in response.text
    assert "Client Inventory" in response.text
    assert "Adapter Capabilities" in response.text
    assert "Latest Scan" in response.text


def test_device_actions_rendering_official_mode_disables_block(monkeypatch):
    db = TestingSessionLocal()
    device = Device(mac="00:11:22:33:44:55", status="unknown")
    db.add(device)
    db.commit()
    device_id = device.id
    db.close()

    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=OfficialUniFiAdapter)
    mock_adapter.capabilities = OfficialUniFiAdapter.capabilities
    factory._shared_adapter = mock_adapter

    response = client.get(f"/htmx/devices/{device_id}/actions", headers=AUTH)
    assert response.status_code == 200
    # Block button should be rendered as disabled with Unavailable badge
    assert "disabled" in response.text
    assert "Unavailable" in response.text
    assert "Trust Device" in response.text
    assert "Ignore Device" in response.text


def test_device_actions_rendering_legacy_mode_enables_block(monkeypatch):
    db = TestingSessionLocal()
    device = Device(mac="00:11:22:33:44:55", status="unknown")
    db.add(device)
    db.commit()
    device_id = device.id
    db.close()

    import app.unifi.factory as factory

    mock_adapter = MagicMock(spec=LegacyUniFiAdapter)
    mock_adapter.capabilities = LegacyUniFiAdapter.capabilities
    factory._shared_adapter = mock_adapter

    response = client.get(f"/htmx/devices/{device_id}/actions", headers=AUTH)
    assert response.status_code == 200
    # Block button should have hx-get pointing to block_modal
    assert "block_modal" in response.text
    assert "Trust Device" in response.text
    assert "Ignore Device" in response.text
