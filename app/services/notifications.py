"""Behavioral notification service: rule management, cooldown tracking, and alert formatting."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Device, Event, Setting

logger = logging.getLogger(__name__)

SETTING_NOTIFY_DISCONNECTED = "notify_device_disconnected"
SETTING_NOTIFY_RECONNECTED = "notify_device_reconnected"
SETTING_NOTIFY_SSID_CHANGED = "notify_device_ssid_changed"
SETTING_NOTIFY_AP_CHANGED = "notify_device_ap_changed"
SETTING_NOTIFY_NETWORK_CHANGED = "notify_device_network_changed"
SETTING_BEHAVIORAL_COOLDOWN = "behavioral_alert_cooldown_seconds"

BEHAVIORAL_EVENT_SETTINGS_MAP = {
    "device_disconnected": SETTING_NOTIFY_DISCONNECTED,
    "device_reconnected": SETTING_NOTIFY_RECONNECTED,
    "device_ssid_changed": SETTING_NOTIFY_SSID_CHANGED,
    "device_ap_changed": SETTING_NOTIFY_AP_CHANGED,
    "device_network_changed": SETTING_NOTIFY_NETWORK_CHANGED,
}


def _parse_bool_setting(val: str | None, default: bool) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "on")


def format_duration(seconds: float | int | None) -> str:
    """Format duration in seconds into a concise human-readable string."""
    if seconds is None:
        return "N/A"
    sec = max(0, int(seconds))
    if sec < 60:
        return f"{sec}s"
    minutes = sec // 60
    rem_sec = sec % 60
    if minutes < 60:
        return f"{minutes}m {rem_sec}s" if rem_sec else f"{minutes}m"
    hours = minutes // 60
    rem_min = minutes % 60
    if hours < 24:
        return f"{hours}h {rem_min}m" if rem_min else f"{hours}h"
    days = hours // 24
    rem_hours = hours % 24
    return f"{days}d {rem_hours}h" if rem_hours else f"{days}d"


@dataclass
class BehavioralNotificationRules:
    notify_device_disconnected: bool = False
    notify_device_reconnected: bool = False
    notify_device_ssid_changed: bool = False
    notify_device_ap_changed: bool = False
    notify_device_network_changed: bool = False
    cooldown_seconds: int = 3600

    def is_enabled(self, event_type: str) -> bool:
        mapping = {
            "device_disconnected": self.notify_device_disconnected,
            "device_reconnected": self.notify_device_reconnected,
            "device_ssid_changed": self.notify_device_ssid_changed,
            "device_ap_changed": self.notify_device_ap_changed,
            "device_network_changed": self.notify_device_network_changed,
        }
        return mapping.get(event_type, False)


def get_behavioral_notification_rules(db: Session | None = None) -> BehavioralNotificationRules:
    """Load behavioral notification rules from database settings, falling back to config defaults."""
    rules = BehavioralNotificationRules(
        notify_device_disconnected=settings.NOTIFY_DEVICE_DISCONNECTED,
        notify_device_reconnected=settings.NOTIFY_DEVICE_RECONNECTED,
        notify_device_ssid_changed=settings.NOTIFY_DEVICE_SSID_CHANGED,
        notify_device_ap_changed=settings.NOTIFY_DEVICE_AP_CHANGED,
        notify_device_network_changed=settings.NOTIFY_DEVICE_NETWORK_CHANGED,
        cooldown_seconds=settings.BEHAVIORAL_ALERT_COOLDOWN_SECONDS,
    )

    if db is None:
        return rules

    try:
        keys = list(BEHAVIORAL_EVENT_SETTINGS_MAP.values()) + [SETTING_BEHAVIORAL_COOLDOWN]
        rows = db.query(Setting).filter(Setting.key.in_(keys)).all()
        settings_dict = {r.key: r.value for r in rows}

        if SETTING_NOTIFY_DISCONNECTED in settings_dict:
            rules.notify_device_disconnected = _parse_bool_setting(
                settings_dict[SETTING_NOTIFY_DISCONNECTED],
                rules.notify_device_disconnected,
            )
        if SETTING_NOTIFY_RECONNECTED in settings_dict:
            rules.notify_device_reconnected = _parse_bool_setting(
                settings_dict[SETTING_NOTIFY_RECONNECTED],
                rules.notify_device_reconnected,
            )
        if SETTING_NOTIFY_SSID_CHANGED in settings_dict:
            rules.notify_device_ssid_changed = _parse_bool_setting(
                settings_dict[SETTING_NOTIFY_SSID_CHANGED],
                rules.notify_device_ssid_changed,
            )
        if SETTING_NOTIFY_AP_CHANGED in settings_dict:
            rules.notify_device_ap_changed = _parse_bool_setting(
                settings_dict[SETTING_NOTIFY_AP_CHANGED],
                rules.notify_device_ap_changed,
            )
        if SETTING_NOTIFY_NETWORK_CHANGED in settings_dict:
            rules.notify_device_network_changed = _parse_bool_setting(
                settings_dict[SETTING_NOTIFY_NETWORK_CHANGED],
                rules.notify_device_network_changed,
            )
        if SETTING_BEHAVIORAL_COOLDOWN in settings_dict:
            raw_cd = settings_dict[SETTING_BEHAVIORAL_COOLDOWN]
            if raw_cd and raw_cd.strip().isdigit():
                rules.cooldown_seconds = max(0, min(604800, int(raw_cd.strip())))
    except Exception as exc:
        logger.warning("Failed to load behavioral notification rules from database: %s", exc)

    return rules


def save_behavioral_notification_rules(db: Session, rules: BehavioralNotificationRules) -> None:
    """Save behavioral notification rules to the database Setting table."""

    def _upsert(key: str, value: str) -> None:
        row = db.query(Setting).filter(Setting.key == key).first()
        if not row:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value

    _upsert(SETTING_NOTIFY_DISCONNECTED, "true" if rules.notify_device_disconnected else "false")
    _upsert(SETTING_NOTIFY_RECONNECTED, "true" if rules.notify_device_reconnected else "false")
    _upsert(SETTING_NOTIFY_SSID_CHANGED, "true" if rules.notify_device_ssid_changed else "false")
    _upsert(SETTING_NOTIFY_AP_CHANGED, "true" if rules.notify_device_ap_changed else "false")
    _upsert(
        SETTING_NOTIFY_NETWORK_CHANGED,
        "true" if rules.notify_device_network_changed else "false",
    )
    _upsert(SETTING_BEHAVIORAL_COOLDOWN, str(rules.cooldown_seconds))


def batch_devices_in_cooldown(
    db: Session,
    pairs: list[tuple[int, str]],
    cooldown_seconds: int,
    now: datetime,
) -> set[tuple[int, str]]:
    """Return the set of (device_id, event_type) tuples currently in active cooldown.

    Reconnection alerts are tracked independently of disconnections so that sending
    a disconnection notification does NOT suppress an upcoming reconnection.
    """
    if not pairs or cooldown_seconds <= 0:
        return set()

    cutoff = now - timedelta(seconds=cooldown_seconds)
    device_ids = list({p[0] for p in pairs})

    events = (
        db.query(Event)
        .filter(
            Event.device_id.in_(device_ids),
            Event.event_type == "alert_sent",
            Event.created_at >= cutoff,
        )
        .all()
    )

    in_cooldown: set[tuple[int, str]] = set()
    for ev in events:
        dev_id = ev.device_id
        if dev_id is None:
            continue
        meta = ev.metadata_json
        alert_event_type = (
            meta.get("event_type", "discovered") if isinstance(meta, dict) else "discovered"
        )
        in_cooldown.add((dev_id, alert_event_type))

    return in_cooldown


def is_in_behavioral_cooldown(
    db: Session,
    device_id: int,
    event_type: str,
    cooldown_seconds: int,
    now: datetime,
) -> bool:
    """Check if an individual (device_id, event_type) is in cooldown."""
    if cooldown_seconds <= 0:
        return False
    in_cd = batch_devices_in_cooldown(db, [(device_id, event_type)], cooldown_seconds, now)
    return (device_id, event_type) in in_cd


def format_behavioral_alert_message(
    event_type: str,
    device: Device,
    metadata: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> str:
    """Format human-readable notification message for behavioral events.

    Guarantees no sensitive controller secrets, keys, or tokens are exposed.
    """
    display_name = device.display_name or device.hostname or device.name or "Unknown Device"
    ts = (
        now.strftime("%Y-%m-%d %H:%M:%S UTC")
        if now
        else datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    )
    meta = metadata or {}

    if event_type == "device_disconnected":
        online_duration = format_duration(meta.get("online_duration_seconds"))
        last_seen = meta.get("last_seen_at") or "N/A"
        return (
            f"Device Disconnected\n"
            f"Name: {display_name}\n"
            f"MAC: {device.mac}\n"
            f"IP: {device.ip or 'N/A'}\n"
            f"State: online -> offline\n"
            f"Disconnected: {ts}\n"
            f"Last Observed: {last_seen}\n"
            f"Online Duration: {online_duration}"
        )

    if event_type == "device_reconnected":
        offline_duration = format_duration(meta.get("offline_duration_seconds"))
        return (
            f"Device Reconnected\n"
            f"Name: {display_name}\n"
            f"MAC: {device.mac}\n"
            f"IP: {device.ip or 'N/A'}\n"
            f"State: offline -> online\n"
            f"Reconnected: {ts}\n"
            f"Offline Duration: {offline_duration}"
        )

    if event_type == "device_ssid_changed":
        old_ssid = meta.get("old_ssid") or "N/A"
        new_ssid = meta.get("new_ssid") or "N/A"
        return (
            f"Device Wi-Fi Network Changed\n"
            f"Name: {display_name}\n"
            f"MAC: {device.mac}\n"
            f"SSID: {old_ssid} -> {new_ssid}\n"
            f"Timestamp: {ts}"
        )

    if event_type == "device_ap_changed":
        old_ap = meta.get("old_ap_mac") or "N/A"
        new_ap = meta.get("new_ap_mac") or "N/A"
        return (
            f"Device Access Point Roamed\n"
            f"Name: {display_name}\n"
            f"MAC: {device.mac}\n"
            f"AP: {old_ap} -> {new_ap}\n"
            f"Timestamp: {ts}"
        )

    if event_type == "device_network_changed":
        old_ip = meta.get("old_ip") or "N/A"
        new_ip = meta.get("new_ip") or "N/A"
        return (
            f"Device IP Address Changed\n"
            f"Name: {display_name}\n"
            f"MAC: {device.mac}\n"
            f"IP: {old_ip} -> {new_ip}\n"
            f"Timestamp: {ts}"
        )

    return f"Device Event: {event_type}\nName: {display_name}\nMAC: {device.mac}\nTimestamp: {ts}"
