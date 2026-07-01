import httpx
import logging
from app.notifications.base import NotificationProvider

logger = logging.getLogger(__name__)

class PushoverProvider(NotificationProvider):
    type = "pushover"
    
    def validate_config(self, config: dict) -> bool:
        return "token" in config and "user" in config

    def send(self, message: str, config: dict) -> tuple[bool, int, str, str]:
        url = "https://api.pushover.net/1/messages.json"
        payload = {
            "token": config.get("token"),
            "user": config.get("user"),
            "message": message,
            "title": "NetWatcher Alert",
            "priority": config.get("priority", 0)
        }
        
        try:
            with httpx.Client(timeout=10) as client:
                r = client.post(url, data=payload)
                success = r.status_code == 200
                return success, r.status_code, r.text, ""
        except Exception as e:
            logger.error(f"Pushover send error: {e}")
            return False, 0, "", str(e)
