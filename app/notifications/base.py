import json
from typing import Dict, Any

class NotificationProvider:
    type: str = "base"

    def validate_config(self, config: Dict[str, Any]) -> bool:
        return True

    def send(self, message: str, config: Dict[str, Any]) -> tuple[bool, int, str, str]:
        """
        Returns (success, status_code, response_text, error_message)
        """
        return False, 0, "", "Not implemented"
