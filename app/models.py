from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
import datetime

from app.db import Base

class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    mac = Column(String, unique=True, index=True, nullable=False)
    hostname = Column(String)
    display_name = Column(String)
    ip = Column(String)
    vendor = Column(String)
    status = Column(String, default='unknown', nullable=False)
    first_seen_at = Column(DateTime)
    last_seen_at = Column(DateTime)
    last_site = Column(String)
    last_ssid = Column(String)
    last_ap_mac = Column(String)
    notes = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)

    observations = relationship("Observation", back_populates="device")
    events = relationship("Event", back_populates="device")


class Observation(Base):
    __tablename__ = "observations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    device_id = Column(Integer, ForeignKey("devices.id"))
    mac = Column(String, nullable=False)
    ip = Column(String)
    hostname = Column(String)
    site = Column(String)
    ssid = Column(String)
    ap_mac = Column(String)
    raw_json = Column(String)
    seen_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)

    device = relationship("Device", back_populates="observations")


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    device_id = Column(Integer, ForeignKey("devices.id"))
    event_type = Column(String, nullable=False)
    severity = Column(String, default='info', nullable=False)
    message = Column(String)
    metadata_json = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    device = relationship("Device", back_populates="events")


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    type = Column(String, nullable=False)
    name = Column(String, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    config_json = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey("events.id"))
    channel_id = Column(Integer, ForeignKey("notification_channels.id"))
    success = Column(Boolean, nullable=False)
    status_code = Column(Integer)
    response = Column(String)
    error = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(String)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    actor = Column(String)
    action = Column(String, nullable=False)
    target_type = Column(String)
    target_id = Column(String)
    details_json = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class OuiEntry(Base):
    __tablename__ = "oui_entries"

    mac_prefix = Column(String, primary_key=True, index=True)
    vendor = Column(String, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False)
