"""Security settings persistence (singleton row)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow

SECURITY_SETTINGS_ID = 1
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"
MAX_CIDR_ENTRIES = 100
MAX_HOST_ENTRIES = 50
LOCKOUT_CONFIRMATION_PHRASE = "ALLOW LOCKOUT"
MIN_PASSWORD_LENGTH = 12
USERNAME_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"


class SecuritySettings(Base):
    """Singleton application security policy (always id=1)."""

    __tablename__ = "security_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    authentication_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    authentication_username: Mapped[str] = mapped_column(
        String, nullable=False, default=DEFAULT_USERNAME
    )
    authentication_password_hash: Mapped[str] = mapped_column(String, nullable=False)
    default_credentials_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cidr_restriction_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allowed_cidrs: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    host_restriction_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allowed_hosts: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
