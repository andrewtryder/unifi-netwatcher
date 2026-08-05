import os

from dotenv import load_dotenv

load_dotenv()


def get_env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "").lower()
    if val in ("true", "1", "yes", "y", "t"):
        return True
    if val in ("false", "0", "no", "n", "f"):
        return False
    return default


class Settings:
    APP_ENV: str = os.environ.get("APP_ENV", "production").strip().lower() or "production"
    APP_SECRET_KEY: str = os.environ.get("APP_SECRET_KEY", "change-me")

    # UniFi Settings
    UNIFI_URL: str = os.environ.get("UNIFI_URL", "https://unifi.example.local")
    UNIFI_USERNAME: str = os.environ.get("UNIFI_USERNAME", "admin")
    UNIFI_PASSWORD: str = os.environ.get("UNIFI_PASSWORD", "change-me")
    UNIFI_SITE: str = os.environ.get("UNIFI_SITE", "default")
    # Default verify TLS; disable only with UNIFI_ALLOW_INSECURE_SSL=true
    UNIFI_VERIFY_SSL: bool = get_env_bool("UNIFI_VERIFY_SSL", True)
    UNIFI_CA_BUNDLE: str = os.environ.get("UNIFI_CA_BUNDLE", "").strip()
    UNIFI_ALLOW_INSECURE_SSL: bool = get_env_bool("UNIFI_ALLOW_INSECURE_SSL", False)
    UNIFI_TIMEOUT_SECONDS: int = int(os.environ.get("UNIFI_TIMEOUT_SECONDS", "10"))
    UNIFI_MOCK_MODE: bool = get_env_bool(
        "UNIFI_MOCK_MODE",
        os.environ.get("UNIFI_URL", "https://unifi.example.local") == "https://unifi.example.local",
    )

    # Scanner Settings
    SCAN_INTERVAL_SECONDS: int = int(os.environ.get("SCAN_INTERVAL_SECONDS", "300"))
    ALERT_COOLDOWN_SECONDS: int = int(os.environ.get("ALERT_COOLDOWN_SECONDS", "21600"))

    # Retention (0 = disabled / never prune)
    OBSERVATION_RETENTION_DAYS: int = int(os.environ.get("OBSERVATION_RETENTION_DAYS", "30"))
    EVENT_RETENTION_DAYS: int = int(os.environ.get("EVENT_RETENTION_DAYS", "90"))

    # Comma-separated hostnames allowed for outbound webhooks (fail closed if empty)
    WEBHOOK_ALLOWED_HOSTS: str = os.environ.get("WEBHOOK_ALLOWED_HOSTS", "")

    UNIFI_DRY_RUN_BLOCKS: bool = get_env_bool("UNIFI_DRY_RUN_BLOCKS", True)

    # Emergency CIDR bypass only — does not disable auth or reset passwords.
    SECURITY_RECOVERY_BYPASS: bool = get_env_bool("SECURITY_RECOVERY_BYPASS", False)

    # Import limits
    IMPORT_MAX_BYTES: int = int(os.environ.get("IMPORT_MAX_BYTES", str(256 * 1024)))
    IMPORT_MAX_ROWS: int = int(os.environ.get("IMPORT_MAX_ROWS", "5000"))


settings = Settings()


def validate_unifi_tls_settings() -> None:
    """Refuse insecure UniFi TLS unless explicitly overridden."""
    if settings.UNIFI_MOCK_MODE:
        return
    if settings.UNIFI_VERIFY_SSL:
        return
    if settings.UNIFI_ALLOW_INSECURE_SSL:
        return
    raise RuntimeError(
        "UNIFI_VERIFY_SSL is disabled. Set UNIFI_VERIFY_SSL=true (recommended), "
        "mount a private CA via UNIFI_CA_BUNDLE, or set UNIFI_ALLOW_INSECURE_SSL=true "
        "only for trusted development networks."
    )


def unifi_tls_verify() -> bool | str:
    """httpx verify argument: True, False, or path to CA bundle."""
    if not settings.UNIFI_VERIFY_SSL:
        return False
    if settings.UNIFI_CA_BUNDLE:
        return settings.UNIFI_CA_BUNDLE
    return True
