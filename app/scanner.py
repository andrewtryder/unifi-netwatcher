import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.mac import normalize_mac
from app.models import (
    Device,
    Event,
    NotificationChannel,
    NotificationDelivery,
    Observation,
    OuiEntry,
)
from app.notifications import PROVIDERS
from app.notifications.secrets import decrypt_channel_config
from app.unifi.client import get_unifi_client

logger = logging.getLogger(__name__)

_scan_lock = threading.Lock()

SCAN_BUSY_MESSAGE = "Scan already in progress"


@dataclass
class AlertIntent:
    device_id: int
    mac: str
    message: str


def _as_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _as_bool(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes"}:
            return True
        if lowered in {"0", "false", "no"}:
            return False
    return None


def _client_telemetry(c: dict) -> dict:
    """Extract Device telemetry fields from a UniFi stat/sta client object."""
    rssi = c.get("rssi")
    if rssi is None:
        rssi = c.get("signal")

    return {
        "name": c.get("name"),
        "is_wired": _as_bool(c.get("is_wired")),
        "radio_proto": c.get("radio_proto"),
        "channel": _as_int(c.get("channel")),
        "rssi": _as_int(rssi),
        "satisfaction": _as_int(c.get("satisfaction")),
        "tx_rate_bps": _as_int(c.get("tx_rate")),
        "rx_rate_bps": _as_int(c.get("rx_rate")),
        "tx_bytes_r": _as_int(c.get("tx_bytes-r")),
        "rx_bytes_r": _as_int(c.get("rx_bytes-r")),
        "sw_port": _as_int(c.get("sw_port")),
        "dev_cat": c.get("dev_cat"),
        "dev_family": c.get("dev_family"),
        "dev_vendor": c.get("dev_vendor"),
        "os_name": c.get("os_name"),
    }


def _apply_telemetry(device: Device, telemetry: dict) -> None:
    """Update Device telemetry; use is not None so False/0 are preserved."""
    for key, value in telemetry.items():
        if value is not None:
            setattr(device, key, value)


def _mac_prefix(mac: str) -> str | None:
    if len(mac) >= 8:
        return mac[:8]
    return None


def _batch_load_devices(db: Session, macs: list[str]) -> dict[str, Device]:
    if not macs:
        return {}
    devices = db.query(Device).filter(Device.mac.in_(macs)).all()
    return {d.mac: d for d in devices}


def _batch_load_oui_vendors(db: Session, prefixes: set[str]) -> dict[str, str]:
    if not prefixes:
        return {}
    entries = db.query(OuiEntry).filter(OuiEntry.mac_prefix.in_(prefixes)).all()
    return {e.mac_prefix: e.vendor for e in entries}


def _devices_in_alert_cooldown(db: Session, device_ids: list[int], now: datetime) -> set[int]:
    if not device_ids:
        return set()
    cooldown = timedelta(seconds=settings.ALERT_COOLDOWN_SECONDS)
    cutoff = now - cooldown
    rows = (
        db.query(Event.device_id)
        .filter(
            Event.device_id.in_(device_ids),
            Event.event_type == "alert_sent",
            Event.created_at >= cutoff,
        )
        .distinct()
        .all()
    )
    return {row[0] for row in rows if row[0] is not None}


def _alert_message(device: Device) -> str:
    return (
        f"Unknown device detected!\n"
        f"MAC: {device.mac}\n"
        f"IP: {device.ip or 'N/A'}\n"
        f"Name: {device.hostname or 'N/A'}\n"
        f"Vendor: {device.vendor or 'N/A'}"
    )


def _deliver_queued_alerts(db: Session, intents: list[AlertIntent]) -> None:
    """Send notification HTTP after scan commit; record deliveries in a short TX."""
    if not intents:
        return

    channels = db.query(NotificationChannel).filter(NotificationChannel.enabled.is_(True)).all()
    if not channels:
        return

    for intent in intents:
        queued_event = Event(
            device_id=intent.device_id,
            event_type="alert_queued",
            message=f"Queued alerts for {len(channels)} channels",
        )
        db.add(queued_event)
        db.flush()

        sent_count = 0
        for channel in channels:
            provider = PROVIDERS.get(channel.type)
            if not provider:
                continue

            try:
                config = decrypt_channel_config(channel.config_json)
            except Exception:
                logger.exception("Failed to decrypt config for channel %s", channel.id)
                continue
            success, status_code, response, error = provider.send(intent.message, config)

            delivery = NotificationDelivery(
                event_id=queued_event.id,
                channel_id=channel.id,
                success=success,
                status_code=status_code,
                response=response[:250] if response else None,
                error=error,
            )
            db.add(delivery)
            if success:
                sent_count += 1

        if sent_count > 0:
            db.add(
                Event(
                    device_id=intent.device_id,
                    event_type="alert_sent",
                    message=f"Sent {sent_count} alerts",
                )
            )
        else:
            db.add(
                Event(
                    device_id=intent.device_id,
                    event_type="alert_failed",
                    message="All alerts failed",
                )
            )

    db.commit()


def run_scan(db: Session, source: str = "scheduled") -> dict:
    """Run a UniFi client scan. Process-level lock prevents overlapping scans."""
    if not _scan_lock.acquire(blocking=False):
        logger.info("Scan skipped — already in progress (requested via %s)", source)
        return {
            "success": False,
            "message": SCAN_BUSY_MESSAGE,
            "devices_processed": 0,
            "busy": True,
        }

    try:
        return _run_scan_locked(db, source)
    finally:
        _scan_lock.release()


def _run_scan_locked(db: Session, source: str) -> dict:
    logger.info("Starting UniFi scan (%s)", source)

    scan_start_event = Event(
        event_type="scan_started", severity="info", message=f"Scan started ({source})"
    )
    db.add(scan_start_event)
    db.commit()

    client = get_unifi_client()
    clients_data = client.get_clients()

    if not clients_data and not client.mock_mode:
        message = (
            "Failed to fetch clients from UniFi. "
            "Check UNIFI_URL, credentials, and SSL settings, or set UNIFI_MOCK_MODE=true for testing."
        )
        scan_fail_event = Event(event_type="scan_failed", severity="error", message=message)
        db.add(scan_fail_event)
        db.commit()
        return {"success": False, "message": message, "devices_processed": 0}

    now = datetime.utcnow()

    # Normalize MACs and prepare batch lookups
    prepared: list[tuple[str, dict]] = []
    macs: list[str] = []
    prefixes: set[str] = set()
    for c in clients_data:
        mac = normalize_mac(c.get("mac"))
        if not mac:
            continue
        prepared.append((mac, c))
        macs.append(mac)
        prefix = _mac_prefix(mac)
        if prefix:
            prefixes.add(prefix)

    devices_by_mac = _batch_load_devices(db, macs)
    oui_by_prefix = _batch_load_oui_vendors(db, prefixes)

    # First pass: upsert devices/observations; collect unknown device ids for cooldown
    unknown_candidates: list[Device] = []
    for mac, c in prepared:
        ip = c.get("ip")
        hostname = c.get("hostname")

        vendor = None
        prefix = _mac_prefix(mac)
        if prefix and prefix in oui_by_prefix:
            vendor = oui_by_prefix[prefix]
        if not vendor:
            vendor = c.get("oui")

        site = c.get("site_id")
        ssid = c.get("essid")
        ap_mac = normalize_mac(c.get("ap_mac", ""))
        telemetry = _client_telemetry(c)

        device = devices_by_mac.get(mac)
        is_new = False
        if not device:
            is_new = True
            device = Device(
                mac=mac,
                hostname=hostname,
                ip=ip,
                vendor=vendor,
                status="unknown",
                first_seen_at=now,
                last_seen_at=now,
                last_site=site,
                last_ssid=ssid,
                last_ap_mac=ap_mac,
                **{k: v for k, v in telemetry.items() if v is not None},
            )
            db.add(device)
            db.flush()
            devices_by_mac[mac] = device
        else:
            device.last_seen_at = now
            if hostname:
                device.hostname = hostname
            if ip:
                device.ip = ip
            if vendor:
                device.vendor = vendor
            if site:
                device.last_site = site
            if ssid:
                device.last_ssid = ssid
            if ap_mac:
                device.last_ap_mac = ap_mac
            _apply_telemetry(device, telemetry)

        db.add(
            Observation(
                device_id=device.id,
                mac=mac,
                ip=ip,
                hostname=hostname,
                site=site,
                ssid=ssid,
                ap_mac=ap_mac,
                raw_json=json.dumps(c),
                seen_at=now,
            )
        )

        if is_new:
            db.add(
                Event(
                    device_id=device.id,
                    event_type="discovered",
                    severity="info",
                    message=f"New device discovered: {mac}",
                )
            )

        if device.status == "unknown":
            unknown_candidates.append(device)

    cooldown_ids = _devices_in_alert_cooldown(db, [d.id for d in unknown_candidates], now)
    alert_intents: list[AlertIntent] = []
    for device in unknown_candidates:
        if device.id in cooldown_ids:
            logger.debug("Device %s is in alert cooldown.", device.mac)
            continue
        alert_intents.append(
            AlertIntent(
                device_id=device.id,
                mac=device.mac,
                message=_alert_message(device),
            )
        )
        # Prevent duplicate intents for the same device within one scan
        cooldown_ids.add(device.id)

    message = f"Scan finished ({source}). Processed {len(clients_data)} clients."
    db.add(Event(event_type="scan_finished", severity="info", message=message))
    db.commit()
    logger.info("Scan finished successfully")

    # HTTP notifications outside the scan write window
    try:
        _deliver_queued_alerts(db, alert_intents)
    except Exception:
        logger.exception("Failed to deliver scan alerts")

    return {"success": True, "message": message, "devices_processed": len(clients_data)}
