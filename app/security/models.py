"""Security settings persistence (singleton row)."""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.db import Base

SECURITY_SETTINGS_ID = 1
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"
MAX_CIDR_ENTRIES = 100
LOCKOUT_CONFIRMATION_PHRASE = "ALLOW LOCKOUT"
MIN_PASSWORD_LENGTH = 12


class SecuritySettings(Base):
    """Singleton application security policy (always id=1)."""

    __tablename__ = "security_settings"

    id = Column(Integer, primary_key=True)
    authentication_enabled = Column(Boolean, nullable=False, default=True)
    authentication_username = Column(String, nullable=False, default=DEFAULT_USERNAME)
    authentication_password_hash = Column(String, nullable=False)
    default_credentials_active = Column(Boolean, nullable=False, default=True)
    cidr_restriction_enabled = Column(Boolean, nullable=False, default=False)
    allowed_cidrs = Column(Text, nullable=False, default="[]")
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        nullable=False,
    )
