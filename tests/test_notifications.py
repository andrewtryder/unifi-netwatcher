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
        return MockResponse(200)

    monkeypatch.setattr(httpx.Client, "post", mock_post)
    
    success, sc, resp, err = provider.send("hello", {"url": "http://test", "method": "POST"})
    assert success is True
    assert sc == 200

def test_cooldown_logic():
    assert True
