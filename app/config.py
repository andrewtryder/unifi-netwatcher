from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import (
    BeforeValidator,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["production", "development", "test"]
_VALID_APP_ENVS = frozenset({"production", "development", "test"})

_PLACEHOLDER_SECRETS = frozenset(
    {
        "",
        "change-me",
        "changeme",
        "secret",
        "password",
        "admin",
        "default",
        "your-secret-key",
        "replace-me",
    }
)


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        raise ValueError("Boolean value is required")
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes", "y", "t", "on"):
            return True
        if normalized in ("false", "0", "no", "n", "f", "off"):
            return False
    raise ValueError(f"Invalid boolean value: {value!r}")


StrictBool = Annotated[bool, BeforeValidator(_parse_bool)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    APP_ENV: AppEnv = "production"
    APP_SECRET_KEY: SecretStr = SecretStr("")
    APP_SECRET_KEY_PATH: str = "data/app-secret.key"

    # Absolute browser origin (scheme://host[:port]) when behind an HTTPS reverse proxy.
    # Used for same-origin checks; leave empty for direct LAN access.
    PUBLIC_ORIGIN: str = ""

    UNIFI_URL: str = "https://unifi.example.local"
    UNIFI_USERNAME: str = "admin"
    UNIFI_PASSWORD: SecretStr = SecretStr("change-me")
    UNIFI_SITE: str = "default"
    UNIFI_VERIFY_SSL: StrictBool = True
    UNIFI_CA_BUNDLE: str = ""
    UNIFI_TIMEOUT_SECONDS: int = Field(default=10, ge=1, le=300)
    UNIFI_MOCK_MODE: StrictBool | None = None
    UNIFI_DRY_RUN_BLOCKS: StrictBool = True

    SCAN_INTERVAL_SECONDS: int = Field(default=300, ge=1, le=86400)
    ALERT_COOLDOWN_SECONDS: int = Field(default=21600, ge=0, le=604800)

    OBSERVATION_RETENTION_DAYS: int = Field(default=30, ge=0, le=3650)
    EVENT_RETENTION_DAYS: int = Field(default=90, ge=0, le=3650)

    IMPORT_MAX_BYTES: int = Field(default=1_000_000, ge=1024, le=50_000_000)
    IMPORT_MAX_ROWS: int = Field(default=5_000, ge=1, le=100_000)

    SECURITY_RECOVERY_BYPASS: StrictBool = False

    # Comma-separated hostnames allowed for webhooks (bypasses private-IP block).
    WEBHOOK_ALLOWED_HOSTS: str = ""

    @field_validator("APP_ENV", mode="before")
    @classmethod
    def _normalize_app_env(cls, value: object) -> str:
        if value is None or value == "":
            return "production"
        normalized = str(value).strip().lower()
        if normalized not in _VALID_APP_ENVS:
            raise ValueError(
                f"APP_ENV must be one of {sorted(_VALID_APP_ENVS)}, got {normalized!r}"
            )
        return normalized

    @field_validator("PUBLIC_ORIGIN")
    @classmethod
    def _validate_public_origin(cls, value: str) -> str:
        origin = (value or "").strip().rstrip("/")
        if not origin:
            return ""
        parsed = urlparse(origin)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                "PUBLIC_ORIGIN must be an absolute origin like "
                "https://netwatcher.home.arpa (scheme://host[:port], no path)"
            )
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.params:
            raise ValueError("PUBLIC_ORIGIN must not include a path, query, or fragment")
        return f"{parsed.scheme}://{parsed.netloc}"

    @field_validator("UNIFI_CA_BUNDLE")
    @classmethod
    def _validate_ca_bundle(cls, value: str) -> str:
        path = (value or "").strip()
        if not path:
            return ""
        from pathlib import Path

        if not Path(path).is_file():
            raise ValueError(f"UNIFI_CA_BUNDLE path does not exist: {path}")
        return path

    @model_validator(mode="after")
    def _finalize(self) -> Settings:
        if self.UNIFI_MOCK_MODE is None:
            object.__setattr__(
                self,
                "UNIFI_MOCK_MODE",
                self.UNIFI_URL.rstrip("/") == "https://unifi.example.local",
            )

        secret = self.APP_SECRET_KEY.get_secret_value().strip()
        if self.APP_ENV == "production" and secret:
            # Empty APP_SECRET_KEY is allowed: a persisted data/app-secret.key is used
            # (or generated on first start). Non-empty values must not be placeholders.
            if secret.lower() in _PLACEHOLDER_SECRETS or len(secret) < 16:
                raise ValueError(
                    "APP_SECRET_KEY must be a non-placeholder value of at least "
                    "16 characters when set in production (or leave it empty to use "
                    "a generated data/app-secret.key)"
                )
        if self.APP_ENV == "production":
            password = self.UNIFI_PASSWORD.get_secret_value().strip().lower()
            if (
                not self.UNIFI_MOCK_MODE
                and password in _PLACEHOLDER_SECRETS
                and self.UNIFI_URL.rstrip("/") != "https://unifi.example.local"
            ):
                raise ValueError(
                    "UNIFI_PASSWORD must not be a placeholder value in production "
                    "when connecting to a real controller"
                )
        return self

    @property
    def webhook_allowed_hosts(self) -> set[str]:
        if not self.WEBHOOK_ALLOWED_HOSTS.strip():
            return set()
        return {h.strip().lower() for h in self.WEBHOOK_ALLOWED_HOSTS.split(",") if h.strip()}

    def unifi_password(self) -> str:
        return self.UNIFI_PASSWORD.get_secret_value()

    def app_secret_key(self) -> str:
        return self.APP_SECRET_KEY.get_secret_value()

    def unifi_verify(self) -> bool | str:
        """httpx ``verify`` value: False, True, or path to a CA bundle."""
        if not self.UNIFI_VERIFY_SSL:
            return False
        bundle = self.UNIFI_CA_BUNDLE.strip()
        if bundle:
            return bundle
        return True


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        raise SystemExit(f"Invalid configuration:\n{exc}") from exc


settings = load_settings()
