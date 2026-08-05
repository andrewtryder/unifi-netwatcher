"""Display helpers for device inventory and dashboard panels."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.db import utcnow
from app.models import Device, Setting
from app.unifi.band import band_label, connection_mix_key

SCAN_INTERVAL_SETTING_KEY = "scan_interval_seconds"
OBSERVATION_RETENTION_SETTING_KEY = "observation_retention_days"
EVENT_RETENTION_SETTING_KEY = "event_retention_days"

# Material Symbols Outlined names keyed by UniFi-ish fingerprint categories.
_DEV_CAT_ICONS: dict[str, str] = {
    "phone": "smartphone",
    "smartphone": "smartphone",
    "mobile": "smartphone",
    "laptop": "laptop",
    "computer": "computer",
    "desktop": "computer",
    "pc": "computer",
    "tablet": "tablet",
    "streaming": "tv",
    "tv": "tv",
    "media": "tv",
    "printer": "print",
    "print": "print",
    "camera": "photo_camera",
    "security": "photo_camera",
    "game": "sports_esports",
    "console": "sports_esports",
    "gaming": "sports_esports",
    "iot": "sensors",
    "sensor": "sensors",
    "appliance": "kitchen",
    "speaker": "speaker",
    "watch": "watch",
    "network": "router",
    "router": "router",
}


def _resolve_non_negative_int_setting(db: Session, key: str, env_default: int) -> tuple[int, str]:
    """Return (value, source). Stored ``0`` is a valid override (disabled)."""
    row = db.query(Setting).filter(Setting.key == key).first()
    if row and row.value is not None and str(row.value).strip():
        try:
            value = int(row.value)
            if value >= 0:
                return value, "stored override"
        except TypeError, ValueError:
            pass
    return env_default, "environment default"


def resolve_scan_interval(db: Session) -> tuple[int, str]:
    """Return (seconds, source) where source is 'stored override' or 'environment default'."""
    row = db.query(Setting).filter(Setting.key == SCAN_INTERVAL_SETTING_KEY).first()
    if row and row.value is not None and str(row.value).strip():
        try:
            seconds = int(row.value)
            if seconds > 0:
                return seconds, "stored override"
        except TypeError, ValueError:
            pass
    return settings.SCAN_INTERVAL_SECONDS, "environment default"


def resolve_observation_retention(db: Session) -> tuple[int, str]:
    """Return (days, source). ``0`` disables observation pruning."""
    return _resolve_non_negative_int_setting(
        db, OBSERVATION_RETENTION_SETTING_KEY, settings.OBSERVATION_RETENTION_DAYS
    )


def resolve_event_retention(db: Session) -> tuple[int, str]:
    """Return (days, source). ``0`` disables event pruning."""
    return _resolve_non_negative_int_setting(
        db, EVENT_RETENTION_SETTING_KEY, settings.EVENT_RETENTION_DAYS
    )


def device_display_name(device: Device) -> str:
    return device.display_name or device.name or device.hostname or device.mac


def device_fingerprint_subtitle(device: Device) -> str:
    parts = [p for p in (device.dev_vendor, device.os_name, device.dev_family) if p]
    if parts:
        return " · ".join(parts)
    return device.vendor or ""


def device_icon(device: Device) -> str:
    cat = (device.dev_cat or device.dev_family or "").strip().lower()
    if not cat:
        return "devices_other"
    for key, icon in _DEV_CAT_ICONS.items():
        if key in cat:
            return icon
    return "devices_other"


def satisfaction_color_class(score: int | None) -> str:
    if score is None:
        return "text-on-surface-variant"
    if score >= 90:
        return "text-secondary"
    if score >= 70:
        return "text-warning"
    return "text-error"


def bytes_r_to_mbps(value: int | None) -> float:
    if not value:
        return 0.0
    return round((value * 8) / 1_000_000, 2)


def connection_subtitle(device: Device) -> str:
    if device.is_wired:
        if device.sw_port is not None:
            return f"Wired · port {device.sw_port}"
        return "Wired"
    band = band_label(
        is_wired=device.is_wired,
        radio_proto=device.radio_proto,
        channel=device.channel,
    )
    if device.rssi is not None:
        return f"{band} · {device.rssi} dBm"
    return band


def online_devices(db: Session, now: datetime | None = None) -> list[Device]:
    """Devices seen within 2× the active scan interval (not the all-time inventory count)."""
    now = now or utcnow()
    interval, _ = resolve_scan_interval(db)
    cutoff = now - timedelta(seconds=max(interval, 1) * 2)
    return (
        db.query(Device)
        .filter(Device.last_seen_at.isnot(None), Device.last_seen_at >= cutoff)
        .all()
    )


def build_dashboard_metrics(db: Session) -> dict:
    """Compute enrichment panels for the dashboard route."""
    online = online_devices(db)

    scored = [d for d in online if d.satisfaction is not None]
    good = sum(1 for d in scored if d.satisfaction >= 90)
    fair = sum(1 for d in scored if 70 <= d.satisfaction < 90)
    poor = sum(1 for d in scored if d.satisfaction < 70)
    avg_satisfaction = None
    if scored:
        avg_satisfaction = round(sum(d.satisfaction for d in scored) / len(scored))

    total_rx = sum(d.rx_bytes_r or 0 for d in online)
    total_tx = sum(d.tx_bytes_r or 0 for d in online)

    mix = {"wired": 0, "band_24": 0, "band_5": 0, "band_6": 0, "other": 0}
    for d in online:
        key = connection_mix_key(is_wired=d.is_wired, radio_proto=d.radio_proto, channel=d.channel)
        mix[key] = mix.get(key, 0) + 1

    attention = sorted(scored, key=lambda d: d.satisfaction)[:5]
    attention_devices = [
        {
            "id": d.id,
            "name": device_display_name(d),
            "icon": device_icon(d),
            "subtitle": connection_subtitle(d),
            "satisfaction": d.satisfaction,
            "color": satisfaction_color_class(d.satisfaction),
        }
        for d in attention
        if d.satisfaction is not None and d.satisfaction < 90
    ]

    mix_total = sum(mix.values()) or 1
    mix_pct = {k: round((v / mix_total) * 100) for k, v in mix.items()}

    return {
        "avg_satisfaction": avg_satisfaction,
        "avg_satisfaction_color": satisfaction_color_class(avg_satisfaction),
        "satisfaction_buckets": {
            "good": good,
            "fair": fair,
            "poor": poor,
            "scored_total": len(scored),
        },
        "total_down_mbps": bytes_r_to_mbps(total_rx),
        "total_up_mbps": bytes_r_to_mbps(total_tx),
        "connection_mix": mix,
        "connection_mix_pct": mix_pct,
        "attention_devices": attention_devices,
        "online_count": len(online),
    }
