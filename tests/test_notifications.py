import httpx
import pytest
from app.config import settings
from app.notifications.secrets import (
    decrypt_channel_config,
    encrypt_channel_config,
    redact_config_for_log,
)
from app.notifications.ssrf import validate_webhook_url
from app.notifications.webhook import WebhookProvider


class MockResponse:
    def __init__(self, status_code, text="mocked"):
        self.status_code = status_code
        self.text = text


@pytest.fixture(autouse=True)
def allow_hooks_example(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_ALLOWED_HOSTS", "hooks.example.com")
    monkeypatch.setattr(
        "app.notifications.ssrf.hostname_resolves_to_public",
        lambda host: True,
    )


def test_webhook_provider(monkeypatch):
    provider = WebhookProvider()

    assert provider.validate_config({"url": "https://hooks.example.com/h", "method": "POST"})
    assert not provider.validate_config({})
    assert not provider.validate_config({"url": "http://hooks.example.com/h", "method": "POST"})
    assert not provider.validate_config({"url": "https://hooks.example.com/h", "method": "GET"})

    def mock_post(self, url, json=None, headers=None):
        assert url == "https://hooks.example.com/h"
        assert json == {"text": "hello"}
        return MockResponse(200)

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    success, sc, resp, err = provider.send(
        "hello", {"url": "https://hooks.example.com/h", "method": "POST"}
    )
    assert success is True
    assert sc == 200


def test_webhook_custom_body_template(monkeypatch):
    provider = WebhookProvider()

    def mock_post(self, url, json=None, headers=None):
        assert json == {"content": "alert text"}
        return MockResponse(201)

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    success, sc, _, _ = provider.send(
        "alert text",
        {
            "url": "https://hooks.example.com/h",
            "method": "POST",
            "body_template": '{"content": "{{message}}"}',
        },
    )
    assert success is True
    assert sc == 201


def test_webhook_rejects_private_and_non_allowlisted():
    monkeypatch_hosts = settings.WEBHOOK_ALLOWED_HOSTS
    assert monkeypatch_hosts == "hooks.example.com"
    ok, err = validate_webhook_url("https://evil.example/x")
    assert ok is False
    assert "WEBHOOK_ALLOWED_HOSTS" in err or "not in" in err


def test_webhook_get_method_rejected():
    provider = WebhookProvider()
    success, sc, _, err = provider.send(
        "hello", {"url": "https://hooks.example.com/h", "method": "GET"}
    )
    assert success is False
    assert sc == 0


def test_encrypt_decrypt_roundtrip(monkeypatch):
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "test-secret-key")
    original = {"token": "abc", "user": "u1", "url": "https://hooks.example.com"}
    stored = encrypt_channel_config(original)
    assert stored.startswith("enc:v1:")
    assert "abc" not in stored
    assert decrypt_channel_config(stored) == original
    # Legacy plaintext still loads
    assert decrypt_channel_config('{"token":"plain"}') == {"token": "plain"}
    redacted = redact_config_for_log(original)
    assert redacted["token"] == "[redacted]"
    assert redacted["user"] == "[redacted]"
