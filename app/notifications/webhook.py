import json
import logging

from app.notifications.base import NotificationProvider
from app.notifications.http import get_notification_http_client, truncate_response_text
from app.notifications.ssrf import sanitize_webhook_headers, validate_webhook_url

logger = logging.getLogger(__name__)

DEFAULT_BODY_TEMPLATE = '{"text": "{{message}}"}'


class WebhookProvider(NotificationProvider):
    type = "webhook"

    def validate_config(self, config: dict) -> bool:
        url = config.get("url")
        if not isinstance(url, str):
            return False
        ok, _ = validate_webhook_url(url)
        if not ok:
            return False
        method = str(config.get("method", "POST")).upper()
        return method == "POST"

    def _build_payload(self, message: str, config: dict) -> dict:
        template = config.get("body_template") or DEFAULT_BODY_TEMPLATE
        body_str = template.replace("{{message}}", message)
        return json.loads(body_str)

    def send(self, message: str, config: dict) -> tuple[bool, int, str, str]:
        if not self.validate_config(config):
            ok, err = validate_webhook_url(config.get("url") or "")
            return False, 0, "", err or "Invalid webhook configuration"

        url = config["url"]
        headers = sanitize_webhook_headers(config.get("headers") or {})
        client = get_notification_http_client()

        try:
            payload = self._build_payload(message, config)
        except json.JSONDecodeError as e:
            return False, 0, "", f"Invalid body template JSON: {e}"

        try:
            r = client.post(url, json=payload, headers=headers)
            body = truncate_response_text(r.text)
            success = r.status_code in (200, 201, 202, 204)
            return success, r.status_code, body, ""
        except Exception as e:
            logger.error("Webhook send error: %s", type(e).__name__)
            return False, 0, "", "Webhook request failed"
