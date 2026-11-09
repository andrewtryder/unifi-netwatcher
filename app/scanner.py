import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.db import utcnow
from app.mac import normalize_mac
from app.models import (
    ConnectivityState,
    Device,
    Event,
    NotificationChannel,
    NotificationDelivery,
    Observation,
    OuiEntry,
)
from app.notifications import PROVIDERS
from app.security.crypto_migrate import load_channel_config
from app.services.connectivity import ConnectivityService, resolve_offline_thresholds
from app.services.notifications import (
    batch_devices_in_cooldown,
    format_behavioral_alert_message,
    get_behavioral_notification_rules,
)
from app.unifi import get_unifi_adapter
from app.unifi.models import UniFiError, as_bool, as_int
from app.unifi.status import sanitize_unifi_error

logger = logging.getLogger(__name__)

_scan_lock = threading.Lock()

SCAN_BUSY_MESSAGE = "Scan already in progress"


@dataclass
class AlertIntent:
    device_id: int
    mac: str
    message: str
    event_type: str = "discovered"
    source_event_id: int | None = None


_as_int = as_int
_as_bool = as_bool


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
        db.query(Event.device_id, Event.metadata_json)
        .filter(
            Event.device_id.in_(device_ids),
            Event.event_type == "alert_sent",
            Event.created_at >= cutoff,
        )
        .all()
    )
    in_cooldown = set()
    for row in rows:
        dev_id = row[0]
        meta = row[1]
        alert_event_type = (
            meta.get("event_type", "discovered") if isinstance(meta, dict) else "discovered"
        )
        if alert_event_type == "discovered" and dev_id is not None:
            in_cooldown.add(dev_id)
    return in_cooldown


def _alert_message(device: Device) -> str:
    return (
        f"Unknown device detected!\n"
        f"MAC: {device.mac}\n"
        f"IP: {device.ip or 'N/A'}\n"
        f"Name: {device.hostname or 'N/A'}\n"
        f"Vendor: {device.vendor or 'N/A'}"
    )


DEFAULT_ALERT_BATCH_LIMIT = 15
DEFAULT_ALERT_BUDGET_SECONDS = 15.0
MAX_CHANNEL_ATTEMPTS = 3


def _queue_alert_intents(
    db: Session, intents: list[AlertIntent], now: datetime | None = None
) -> list[Event]:
    """Persist alert intents into durable alert_queued events in SQLite within reconciliation."""
    if not intents:
        return []
    ts = now or utcnow()
    queued_events: list[Event] = []
    for intent in intents:
        # Prevent duplicate alert_queued events if one already exists for this source_event_id
        if intent.source_event_id is not None:
            existing = (
                db.query(Event)
                .filter(
                    Event.device_id == intent.device_id,
                    Event.event_type == "alert_queued",
                )
                .all()
            )
            already_queued = any(
                isinstance(e.metadata_json, dict)
                and e.metadata_json.get("source_event_id") == intent.source_event_id
                for e in existing
            )
            if already_queued:
                continue

        queued_event = Event(
            device_id=intent.device_id,
            event_type="alert_queued",
            message=intent.message,
            metadata_json={
                "event_type": intent.event_type,
                "source_event_id": intent.source_event_id,
                "mac": intent.mac,
            },
            created_at=ts,
        )
        db.add(queued_event)
        queued_events.append(queued_event)
    db.flush()
    return queued_events


def _deliver_pending_alerts(
    db: Session,
    *,
    batch_limit: int = DEFAULT_ALERT_BATCH_LIMIT,
    budget_seconds: float = DEFAULT_ALERT_BUDGET_SECONDS,
) -> int:
    """Deliver pending alert_queued events to enabled channels with bounded budget and per-channel tracking.

    Returns the number of deliveries attempted in this run.
    """
    channels = db.query(NotificationChannel).filter(NotificationChannel.enabled.is_(True)).all()
    if not channels:
        return 0

    # Bounded lookback to recover recent pending work without scanning ancient events
    cutoff = utcnow() - timedelta(hours=24)
    queued_events = (
        db.query(Event)
        .filter(
            Event.event_type == "alert_queued",
            Event.created_at >= cutoff,
        )
        .order_by(Event.created_at.asc(), Event.id.asc())
        .limit(50)
        .all()
    )
    if not queued_events:
        return 0

    event_ids = [e.id for e in queued_events]
    deliveries = (
        db.query(NotificationDelivery).filter(NotificationDelivery.event_id.in_(event_ids)).all()
    )
    successful_deliveries = {(d.event_id, d.channel_id) for d in deliveries if d.success}
    attempt_counts: dict[tuple[int, int], int] = {}
    for d in deliveries:
        if d.event_id is not None and d.channel_id is not None:
            attempt_counts[(d.event_id, d.channel_id)] = (
                attempt_counts.get((d.event_id, d.channel_id), 0) + 1
            )

    deadline = time.monotonic() + budget_seconds
    deliveries_attempted = 0

    for q_event in queued_events:
        needed_channels = [
            ch
            for ch in channels
            if (q_event.id, ch.id) not in successful_deliveries
            and attempt_counts.get((q_event.id, ch.id), 0) < MAX_CHANNEL_ATTEMPTS
        ]

        if not needed_channels:
            continue

        meta = q_event.metadata_json if isinstance(q_event.metadata_json, dict) else {}
        msg = q_event.message or ""

        for channel in needed_channels:
            if deliveries_attempted >= batch_limit or time.monotonic() >= deadline:
                logger.warning(
                    "Notification delivery budget reached (%d attempts); deferring remaining alerts to next scan.",
                    deliveries_attempted,
                )
                return deliveries_attempted

            provider = PROVIDERS.get(channel.type)
            if not provider:
                delivery = NotificationDelivery(
                    event_id=q_event.id,
                    channel_id=channel.id,
                    success=False,
                    status_code=None,
                    response=None,
                    error=f"Unknown provider type: {channel.type}",
                )
                db.add(delivery)
                db.commit()
                deliveries_attempted += 1
                attempt_counts[(q_event.id, channel.id)] = (
                    attempt_counts.get((q_event.id, channel.id), 0) + 1
                )
                continue

            try:
                config = load_channel_config(channel)
                if not isinstance(config, dict) or not provider.validate_config(config):
                    raise ValueError("Invalid channel configuration")
                success, status_code, response, error = provider.send(msg, config)
            except Exception as exc:
                logger.warning(
                    "Notification channel %s failed for event %s: %s",
                    channel.id,
                    q_event.id,
                    exc,
                )
                success, status_code, response, error = False, 0, "", str(exc)

            delivery = NotificationDelivery(
                event_id=q_event.id,
                channel_id=channel.id,
                success=success,
                status_code=status_code,
                response=response[:250] if response else None,
                error=error,
            )
            db.add(delivery)
            db.commit()
            deliveries_attempted += 1
            attempt_counts[(q_event.id, channel.id)] = (
                attempt_counts.get((q_event.id, channel.id), 0) + 1
            )
            if success:
                successful_deliveries.add((q_event.id, channel.id))

        # Record summary event for this attempt pass
        successful_in_pass = [
            ch for ch in needed_channels if (q_event.id, ch.id) in successful_deliveries
        ]
        if successful_in_pass:
            db.add(
                Event(
                    device_id=q_event.device_id,
                    event_type="alert_sent",
                    message=f"Sent {len(successful_in_pass)} alerts",
                    metadata_json={
                        "event_type": meta.get("event_type"),
                        "source_event_id": meta.get("source_event_id"),
                        "queued_event_id": q_event.id,
                        "sent_count": len(successful_in_pass),
                    },
                )
            )
        elif needed_channels:
            db.add(
                Event(
                    device_id=q_event.device_id,
                    event_type="alert_failed",
                    message="All alerts failed",
                    metadata_json={
                        "event_type": meta.get("event_type"),
                        "source_event_id": meta.get("source_event_id"),
                        "queued_event_id": q_event.id,
                    },
                )
            )
        db.commit()

    return deliveries_attempted


def _deliver_queued_alerts(
    db: Session,
    intents: list[AlertIntent] | None = None,
    *,
    batch_limit: int = DEFAULT_ALERT_BATCH_LIMIT,
    budget_seconds: float = DEFAULT_ALERT_BUDGET_SECONDS,
) -> None:
    """Queues new intents if provided, and delivers pending alerts within budget."""
    if intents:
        _queue_alert_intents(db, intents)
        db.commit()
    _deliver_pending_alerts(db, batch_limit=batch_limit, budget_seconds=budget_seconds)


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

    try:
        client = get_unifi_adapter()
        clients_data = client.get_clients()
    except UniFiError as exc:
        clean_msg = sanitize_unifi_error(str(exc))
        message = f"Failed to fetch clients from UniFi: {clean_msg}"
        logger.error(message)
        scan_fail_event = Event(event_type="scan_failed", severity="error", message=message)
        db.add(scan_fail_event)
        db.commit()
        return {"success": False, "message": message, "devices_processed": 0}
    except Exception as exc:
        clean_msg = sanitize_unifi_error(str(exc))
        message = f"Unexpected error communicating with UniFi: {clean_msg}"
        logger.error(message)
        scan_fail_event = Event(event_type="scan_failed", severity="error", message=message)
        db.add(scan_fail_event)
        db.commit()
        return {"success": False, "message": message, "devices_processed": 0}

    now = utcnow()

    # Finding 1: Ensure inventory response is a valid list of mapping entries.
    # None or non-list or malformed items must produce scan_failed without advancing missed-scans.
    if clients_data is None or not isinstance(clients_data, list):
        item_type = type(clients_data).__name__ if clients_data is not None else "None"
        message = f"Invalid inventory response from UniFi: expected list, got {item_type}"
        logger.error(message)
        scan_fail_event = Event(event_type="scan_failed", severity="error", message=message)
        db.add(scan_fail_event)
        db.commit()
        return {"success": False, "message": message, "devices_processed": 0}

    for c in clients_data:
        if not isinstance(c, Mapping):
            message = f"Invalid inventory response from UniFi: malformed device entry ({type(c).__name__})"
            logger.error(message)
            scan_fail_event = Event(event_type="scan_failed", severity="error", message=message)
            db.add(scan_fail_event)
            db.commit()
            return {"success": False, "message": message, "devices_processed": 0}

    # Finding 2: Explicit transaction boundary around reconciliation.
    # Any failure during reconciliation rolls back partial changes and logs scan_failed on a best-effort basis.
    alert_intents: list[AlertIntent] = []
    try:
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

        # Instantiate connectivity service with resolved thresholds
        missed_threshold, grace_threshold = resolve_offline_thresholds(db)
        connectivity = ConnectivityService(
            missed_scans_threshold=missed_threshold,
            grace_seconds=grace_threshold,
        )
        rules = get_behavioral_notification_rules(db)
        behavioral_cooldown_pairs: set[tuple[int, str]] = set()

        # First pass: upsert devices/observations; apply connectivity updates for observed clients
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
                connectivity.handle_discovered_device(device, now)
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

                transition = connectivity.handle_observed_device(device, now)
                if transition and transition.event_type:
                    event = Event(
                        device_id=device.id,
                        event_type=transition.event_type,
                        severity="info",
                        message=transition.event_message,
                        metadata_json=transition.event_metadata,
                        created_at=now,
                    )
                    db.add(event)
                    db.flush()
                    if rules.is_enabled(transition.event_type):
                        active_cooldowns = batch_devices_in_cooldown(
                            db,
                            [(device.id, transition.event_type)],
                            rules.cooldown_seconds,
                            now,
                        )
                        pair = (device.id, transition.event_type)
                        if pair not in active_cooldowns and pair not in behavioral_cooldown_pairs:
                            alert_intents.append(
                                AlertIntent(
                                    device_id=device.id,
                                    mac=device.mac,
                                    message=format_behavioral_alert_message(
                                        transition.event_type,
                                        device,
                                        transition.event_metadata,
                                        now,
                                    ),
                                    event_type=transition.event_type,
                                    source_event_id=event.id,
                                )
                            )
                            behavioral_cooldown_pairs.add(pair)

            db.add(
                Observation(
                    device_id=device.id,
                    mac=mac,
                    ip=ip,
                    hostname=hostname,
                    site=site,
                    ssid=ssid,
                    ap_mac=ap_mac,
                    raw_json=c.raw_data if hasattr(c, "raw_data") else c,
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

        # Finding 4: Evaluate only online absent devices for offline transitions.
        # Historical offline/unknown devices do not change state and do not need missed-scan increments.
        observed_mac_set = set(macs)
        absent_query = db.query(Device).filter(
            Device.connectivity_state == ConnectivityState.ONLINE.value
        )
        if observed_mac_set:
            absent_query = absent_query.filter(Device.mac.notin_(observed_mac_set))
        absent_devices = absent_query.all()

        for device in absent_devices:
            transition = connectivity.handle_absent_device(device, now)
            if transition and transition.event_type:
                event = Event(
                    device_id=device.id,
                    event_type=transition.event_type,
                    severity="info",
                    message=transition.event_message,
                    metadata_json=transition.event_metadata,
                    created_at=now,
                )
                db.add(event)
                db.flush()
                if rules.is_enabled(transition.event_type):
                    active_cooldowns = batch_devices_in_cooldown(
                        db,
                        [(device.id, transition.event_type)],
                        rules.cooldown_seconds,
                        now,
                    )
                    pair = (device.id, transition.event_type)
                    if pair not in active_cooldowns and pair not in behavioral_cooldown_pairs:
                        alert_intents.append(
                            AlertIntent(
                                device_id=device.id,
                                mac=device.mac,
                                message=format_behavioral_alert_message(
                                    transition.event_type,
                                    device,
                                    transition.event_metadata,
                                    now,
                                ),
                                event_type=transition.event_type,
                                source_event_id=event.id,
                            )
                        )
                        behavioral_cooldown_pairs.add(pair)

        cooldown_ids = _devices_in_alert_cooldown(db, [d.id for d in unknown_candidates], now)
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

        # Persist alert intents durably into SQLite before commit
        if alert_intents:
            _queue_alert_intents(db, alert_intents, now)

        if not clients_data:
            message = f"Scan completed: 0 active clients on UniFi site ({source})"
        else:
            message = f"Scan finished ({source}). Processed {len(clients_data)} clients."

        db.add(Event(event_type="scan_finished", severity="info", message=message))
        db.commit()
    except Exception as exc:
        db.rollback()
        clean_msg = sanitize_unifi_error(str(exc))
        message = f"Scan failed during reconciliation: {clean_msg}"
        logger.exception(message)
        try:
            scan_fail_event = Event(event_type="scan_failed", severity="error", message=message)
            db.add(scan_fail_event)
            db.commit()
        except Exception:
            logger.exception("Failed to record scan_failed event after reconciliation error")
            db.rollback()
        return {"success": False, "message": message, "devices_processed": 0}

    logger.info("Scan finished successfully")

    # HTTP notifications outside the scan write window with bounded delivery budget
    try:
        _deliver_pending_alerts(db)
    except Exception:
        logger.exception("Failed to deliver scan alerts")

    return {"success": True, "message": message, "devices_processed": len(clients_data)}
