import os
from typing import Optional

def get_env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "").lower()
    if val in ("true", "1", "yes", "y", "t"):
        return True
    if val in ("false", "0", "no", "n", "f"):
        return False
    return default

class Settings:
    # UniFi Settings
    UNIFI_URL: str = os.environ.get("UNIFI_URL", "https://unifi.example.local")
    UNIFI_USERNAME: str = os.environ.get("UNIFI_USERNAME", "admin")
    UNIFI_PASSWORD: str = os.environ.get("UNIFI_PASSWORD", "change-me")
    UNIFI_SITE: str = os.environ.get("UNIFI_SITE", "default")
    UNIFI_VERIFY_SSL: bool = get_env_bool("UNIFI_VERIFY_SSL", False)
    UNIFI_TIMEOUT_SECONDS: int = int(os.environ.get("UNIFI_TIMEOUT_SECONDS", "10"))
    UNIFI_MOCK_MODE: bool = get_env_bool("UNIFI_MOCK_MODE", False)

    # Scanner Settings
    SCAN_INTERVAL_SECONDS: int = int(os.environ.get("SCAN_INTERVAL_SECONDS", "300"))
    ALERT_COOLDOWN_SECONDS: int = int(os.environ.get("ALERT_COOLDOWN_SECONDS", "21600"))

 
    UNIFI_DRY_RUN_BLOCKS: bool = get_env_bool("UNIFI_DRY_RUN_BLOCKS", True)
settings = Settings()
