import json
import httpx
import logging
from app.notifications.base import NotificationProvider

logger = logging.getLogger(__name__)

DEFAULT_BODY_TEMPLATE = '{"text": "{{message}}"}'

class WebhookProvider(NotificationProvider):
    type = "webhook"
    
    def validate_config(self, config: dict) -> bool:
        return "url" in config

    def _build_payload(self, message: str, config: dict) -> dict:
        template = config.get("body_template") or DEFAULT_BODY_TEMPLATE
        body_str = template.replace("{{message}}", message)
        return json.loads(body_str)

    def send(self, message: str, config: dict) -> tuple[bool, int, str, str]:
        url = config.get("url")
        method = config.get("method", "POST").upper()
        headers = config.get("headers") or {}

        try:
            with httpx.Client(timeout=10) as client:
                if method == "GET":
                    param_name = config.get("query_param", "text")
                    r = client.get(url, params={param_name: message}, headers=headers)
                elif method in ("POST", "PUT"):
                    try:
                        payload = self._build_payload(message, config)
                    except json.JSONDecodeError as e:
                        return False, 0, "", f"Invalid body template JSON: {e}"
                    if method == "POST":
                        r = client.post(url, json=payload, headers=headers)
                    else:
                        r = client.put(url, json=payload, headers=headers)
                else:
                    return False, 0, "", f"Unsupported method: {method}"

                success = r.status_code in (200, 201, 202, 204)
                return success, r.status_code, r.text, ""
        except Exception as e:
            logger.error(f"Webhook send error: {e}")
            return False, 0, "", str(e)
