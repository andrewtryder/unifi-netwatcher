import httpx
import logging
from app.notifications.base import NotificationProvider

logger = logging.getLogger(__name__)

class WebhookProvider(NotificationProvider):
    type = "webhook"
    
    def validate_config(self, config: dict) -> bool:
        return "url" in config

    def send(self, message: str, config: dict) -> tuple[bool, int, str, str]:
        url = config.get("url")
        method = config.get("method", "POST").upper()
        headers = config.get("headers", {})
        
        # message is assumed to be JSON string if webhook, or we wrap it
        payload = {"text": message}
        
        try:
            with httpx.Client(timeout=10) as client:
                if method == "POST":
                    r = client.post(url, json=payload, headers=headers)
                elif method == "PUT":
                    r = client.put(url, json=payload, headers=headers)
                else:
                    return False, 0, "", f"Unsupported method: {method}"
                    
                success = r.status_code in (200, 201, 202, 204)
                return success, r.status_code, r.text, ""
        except Exception as e:
            logger.error(f"Webhook send error: {e}")
            return False, 0, "", str(e)
