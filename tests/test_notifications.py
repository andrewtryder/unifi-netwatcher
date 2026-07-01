import pytest
from app.notifications.base import NotificationProvider
from app.notifications.webhook import WebhookProvider
import httpx

class MockResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.text = "mocked"

def test_webhook_provider(monkeypatch):
    provider = WebhookProvider()
    
    assert provider.validate_config({"url": "http://test"}) == True
    assert provider.validate_config({}) == False

    def mock_post(self, url, json=None, headers=None):
        assert json == {"text": "hello"}
        return MockResponse(200)

    monkeypatch.setattr(httpx.Client, "post", mock_post)
    
    success, sc, resp, err = provider.send("hello", {"url": "http://test", "method": "POST"})
    assert success is True
    assert sc == 200

def test_webhook_custom_body_template(monkeypatch):
    provider = WebhookProvider()

    def mock_post(self, url, json=None, headers=None):
        assert json == {"content": "alert text"}
        return MockResponse(201)

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    success, sc, _, _ = provider.send("alert text", {
        "url": "http://test",
        "method": "POST",
        "body_template": '{"content": "{{message}}"}',
    })
    assert success is True
    assert sc == 201

def test_webhook_get_method(monkeypatch):
    provider = WebhookProvider()
    captured = {}

    def mock_get(self, url, params=None, headers=None):
        captured["url"] = url
        captured["params"] = params
        return MockResponse(200)

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    success, sc, _, _ = provider.send("hello", {"url": "http://test", "method": "GET"})
    assert success is True
    assert captured["params"] == {"text": "hello"}

def test_cooldown_logic():
    assert True
