import httpx
import pytest
from app.notifications.http import MAX_RESPONSE_BYTES, get_notification_http_client
from app.notifications.ssrf import WebhookURLError, validate_webhook_url
from app.notifications.webhook import WebhookProvider, _replace_placeholders


class MockStreamResponse:
    def __init__(self, status_code, body: bytes = b"mocked"):
        self.status_code = status_code
        self._body = body

    def iter_bytes(self):
        # Yield in chunks to exercise the cap path
        chunk_size = 1024
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def allow_webhook_urls(monkeypatch):
    from app.notifications.ssrf import ResolvedWebhookTarget

    def fake_resolve(url, allow_private_network=False):
        return ResolvedWebhookTarget(
            original_url=str(url),
            hostname="hooks.example.com",
            port=443,
            connect_host="203.0.113.10",
            allow_private_network=bool(allow_private_network),
        )

    monkeypatch.setattr("app.notifications.webhook.resolve_webhook_target", fake_resolve)


def test_webhook_provider(monkeypatch, allow_webhook_urls):
    provider = WebhookProvider()

    assert provider.validate_config({"url": "https://hooks.example.com"})
    assert not provider.validate_config({})

    def mock_stream(self, method, url, **kwargs):
        assert method == "POST"
        body = kwargs.get("content") or b""
        assert b'"text":"hello"' in body or b'"text": "hello"' in body
        return MockStreamResponse(200)

    monkeypatch.setattr(httpx.Client, "stream", mock_stream)

    success, sc, resp, err = provider.send(
        "hello", {"url": "https://hooks.example.com", "method": "POST"}
    )
    assert success is True, err
    assert sc == 200
    assert resp == "mocked"


def test_webhook_custom_body_template(monkeypatch, allow_webhook_urls):
    provider = WebhookProvider()

    def mock_stream(self, method, url, **kwargs):
        body = kwargs.get("content") or b""
        assert b'alert "quoted"' in body or b'alert \\"quoted\\"' in body
        return MockStreamResponse(201)

    monkeypatch.setattr(httpx.Client, "stream", mock_stream)

    success, sc, _, err = provider.send(
        'alert "quoted"',
        {
            "url": "https://hooks.example.com",
            "method": "POST",
            "body_template": '{"content": "{{message}}"}',
        },
    )
    assert success is True, err
    assert sc == 201


def test_webhook_template_recursive_replace():
    payload = _replace_placeholders({"text": "{{message}}", "nested": ["{{message}}"]}, 'a"b')
    assert payload == {"text": 'a"b', "nested": ['a"b']}


def test_webhook_get_method(monkeypatch, allow_webhook_urls):
    provider = WebhookProvider()
    captured = {}

    def mock_stream(self, method, url, **kwargs):
        captured["method"] = method
        captured["params"] = kwargs.get("params")
        return MockStreamResponse(200)

    monkeypatch.setattr(httpx.Client, "stream", mock_stream)

    success, sc, _, _ = provider.send(
        "hello", {"url": "https://hooks.example.com", "method": "GET"}
    )
    assert success is True
    assert captured["method"] == "GET"
    assert captured["params"] == {"text": "hello"}


def test_webhook_response_truncated(monkeypatch, allow_webhook_urls):
    provider = WebhookProvider()
    oversized = b"x" * (MAX_RESPONSE_BYTES + 5000)

    def mock_stream(self, method, url, **kwargs):
        return MockStreamResponse(200, oversized)

    monkeypatch.setattr(httpx.Client, "stream", mock_stream)
    success, sc, body, err = provider.send(
        "hello", {"url": "https://hooks.example.com", "method": "POST"}
    )
    assert success is True
    assert len(body.encode("utf-8")) <= MAX_RESPONSE_BYTES


def test_notification_client_ignores_proxy_env(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    from app.notifications import http as http_mod

    http_mod.close_notification_http_client()
    client = get_notification_http_client()
    assert client.trust_env is False
    http_mod.close_notification_http_client()


def test_ssrf_rejects_non_https():
    with pytest.raises(WebhookURLError):
        validate_webhook_url("http://example.com/hook")


def test_ssrf_rejects_loopback():
    with pytest.raises(WebhookURLError):
        validate_webhook_url("https://127.0.0.1/hook")
    with pytest.raises(WebhookURLError):
        validate_webhook_url("https://127.0.0.1/hook", allow_private_network=True)


def test_ssrf_rejects_private_unless_flag():
    with pytest.raises(WebhookURLError):
        validate_webhook_url("https://192.168.1.1/hook")
    assert validate_webhook_url("https://192.168.1.1/hook", allow_private_network=True).startswith(
        "https://"
    )


def test_ssrf_rejects_link_local_and_metadata():
    with pytest.raises(WebhookURLError):
        validate_webhook_url("https://169.254.1.1/hook")
    with pytest.raises(WebhookURLError):
        validate_webhook_url("https://169.254.169.254/hook", allow_private_network=True)


def test_ssrf_allowlist_bypasses_private(monkeypatch):
    class _Settings:
        webhook_allowed_hosts = {"hooks.example.com"}

    monkeypatch.setattr("app.notifications.ssrf.settings", _Settings())

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(None, None, None, None, ("10.0.0.5", port))]

    monkeypatch.setattr("app.notifications.ssrf.socket.getaddrinfo", fake_getaddrinfo)
    assert validate_webhook_url("https://hooks.example.com/hook").startswith("https://")


def test_ssrf_allowlist_still_blocks_loopback_resolve(monkeypatch):
    class _Settings:
        webhook_allowed_hosts = {"hooks.example.com"}

    monkeypatch.setattr("app.notifications.ssrf.settings", _Settings())

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(None, None, None, None, ("127.0.0.1", port))]

    monkeypatch.setattr("app.notifications.ssrf.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(WebhookURLError):
        validate_webhook_url("https://hooks.example.com/hook")


def test_webhook_pins_resolved_ip(monkeypatch):
    """Send must connect to the validated IP, not re-resolve the hostname."""
    from app.notifications import webhook as wh
    from app.notifications.ssrf import ResolvedWebhookTarget

    target = ResolvedWebhookTarget(
        original_url="https://hooks.example.com/hook",
        hostname="hooks.example.com",
        port=443,
        connect_host="203.0.113.10",
        allow_private_network=False,
    )
    monkeypatch.setattr(
        wh,
        "resolve_webhook_target",
        lambda url, allow_private_network=False: target,
    )

    captured = {}

    def mock_stream(self, method, url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers") or {}
        captured["extensions"] = kwargs.get("extensions") or {}
        return MockStreamResponse(200)

    monkeypatch.setattr(httpx.Client, "stream", mock_stream)

    provider = WebhookProvider()
    success, sc, _, _ = provider.send(
        "hello", {"url": "https://hooks.example.com/hook", "method": "POST"}
    )
    assert success is True
    assert captured["url"].startswith("https://203.0.113.10:443/")
    assert captured["headers"].get("Host") == "hooks.example.com"
    assert captured["extensions"].get("sni_hostname") == "hooks.example.com"


def test_alerts_respect_cooldown(monkeypatch):
    """Second scan within cooldown must not duplicate alert sends."""
    from app.config import settings
    from app.db import Base
    from app.models import NotificationChannel
    from app.scanner import run_scan
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()

    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", True)
    monkeypatch.setattr(settings, "ALERT_COOLDOWN_SECONDS", 3600)

    send_calls: list = []

    class Provider:
        def validate_config(self, config):
            return True

        def send(self, message, config):
            send_calls.append(message)
            return True, 200, "ok", ""

    db.add(
        NotificationChannel(
            name="hook",
            type="webhook",
            enabled=True,
            config_json={"url": "https://hooks.example.com/hook"},
        )
    )
    db.commit()
    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": Provider()})

    assert run_scan(db)["success"] is True
    first = len(send_calls)
    assert first > 0
    assert run_scan(db)["success"] is True
    assert len(send_calls) == first
    db.close()
    Base.metadata.drop_all(bind=engine)
