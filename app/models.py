from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db import Base, utcnow


class DeviceStatus(StrEnum):
    UNKNOWN = "unknown"
    TRUSTED = "trusted"
    IGNORED = "ignored"
    BLOCKED = "blocked"


class EventSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ChannelType(StrEnum):
    WEBHOOK = "webhook"
    PUSHOVER = "pushover"


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        Index("ix_devices_status_last_seen_at", "status", "last_seen_at"),
        CheckConstraint(
            "status IN ('unknown','trusted','ignored','blocked')",
            name="ck_devices_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mac: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    hostname: Mapped[str | None] = mapped_column(String, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    ip: Mapped[str | None] = mapped_column(String, nullable=True)
    vendor: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default=DeviceStatus.UNKNOWN.value, nullable=False)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_site: Mapped[str | None] = mapped_column(String, nullable=True)
    last_ssid: Mapped[str | None] = mapped_column(String, nullable=True)
    last_ap_mac: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_wired: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    radio_proto: Mapped[str | None] = mapped_column(String, nullable=True)
    channel: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rssi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    satisfaction: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tx_rate_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rx_rate_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tx_bytes_r: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rx_bytes_r: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sw_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dev_cat: Mapped[str | None] = mapped_column(String, nullable=True)
    dev_family: Mapped[str | None] = mapped_column(String, nullable=True)
    dev_vendor: Mapped[str | None] = mapped_column(String, nullable=True)
    os_name: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    observations: Mapped[list[Observation]] = relationship(
        "Observation", back_populates="device", cascade="all, delete-orphan"
    )
    events: Mapped[list[Event]] = relationship(
        "Event", back_populates="device", cascade="all, delete-orphan"
    )


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (Index("ix_observations_device_id_seen_at", "device_id", "seen_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=True
    )
    mac: Mapped[str] = mapped_column(String, nullable=False)
    ip: Mapped[str | None] = mapped_column(String, nullable=True)
    hostname: Mapped[str | None] = mapped_column(String, nullable=True)
    site: Mapped[str | None] = mapped_column(String, nullable=True)
    ssid: Mapped[str | None] = mapped_column(String, nullable=True)
    ap_mac: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    device: Mapped[Device | None] = relationship("Device", back_populates="observations")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_device_id_event_type_created_at", "device_id", "event_type", "created_at"),
        Index("ix_events_event_type_created_at", "event_type", "created_at"),
        CheckConstraint(
            "severity IN ('info','warning','error')",
            name="ck_events_severity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, default=EventSeverity.INFO.value, nullable=False)
    message: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    device: Mapped[Device | None] = relationship("Device", back_populates="events")
    deliveries: Mapped[list[NotificationDelivery]] = relationship(
        "NotificationDelivery", back_populates="event", cascade="all, delete-orphan"
    )


class NotificationChannel(Base):
    __tablename__ = "notification_channels"
    __table_args__ = (
        CheckConstraint(
            "type IN ('webhook','pushover')",
            name="ck_notification_channels_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    deliveries: Mapped[list[NotificationDelivery]] = relationship(
        "NotificationDelivery", back_populates="channel", cascade="all, delete-orphan"
    )


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        Index("ix_notification_deliveries_event_id", "event_id"),
        Index("ix_notification_deliveries_channel_id", "channel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=True
    )
    channel_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=True
    )
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    event: Mapped[Event | None] = relationship("Event", back_populates="deliveries")
    channel: Mapped[NotificationChannel | None] = relationship(
        "NotificationChannel", back_populates="deliveries"
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    target_type: Mapped[str | None] = mapped_column(String, nullable=True)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True)
    details_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class OuiEntry(Base):
    __tablename__ = "oui_entries"

    mac_prefix: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    vendor: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


from app.security.models import SecuritySettings  # noqa: E402, F401
