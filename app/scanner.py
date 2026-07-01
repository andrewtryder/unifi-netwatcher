import json
import logging
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from app.models import Device, Observation, Event, NotificationChannel, NotificationDelivery, OuiEntry
from app.unifi.client import UnifiClient
from app.mac import normalize_mac
from app.config import settings
from app.notifications import PROVIDERS

logger = logging.getLogger(__name__)

def check_and_send_alerts(db: Session, device: Device, now: datetime):
    # Only alert on unknown devices
    if device.status != "unknown":
        return

    # Check cooldown by looking at recent alert_sent events
    cooldown = timedelta(seconds=settings.ALERT_COOLDOWN_SECONDS)
    recent_alert = db.query(Event).filter(
        Event.device_id == device.id,
        Event.event_type == "alert_sent",
        Event.created_at >= (now - cooldown)
    ).first()
    
    if recent_alert:
        logger.debug(f"Device {device.mac} is in alert cooldown.")
        return
        
    # Send alerts
    channels = db.query(NotificationChannel).filter(NotificationChannel.enabled == True).all()
    if not channels:
        return
        
    message = f"Unknown device detected!\nMAC: {device.mac}\nIP: {device.ip or 'N/A'}\nName: {device.hostname or 'N/A'}\nVendor: {device.vendor or 'N/A'}"
    
    queued_event = Event(device_id=device.id, event_type="alert_queued", message=f"Queued alerts for {len(channels)} channels")
    db.add(queued_event)
    db.flush()

    sent_count = 0
    for channel in channels:
        provider = PROVIDERS.get(channel.type)
        if not provider:
            continue
            
        config = json.loads(channel.config_json)
        success, status_code, response, error = provider.send(message, config)
        
        delivery = NotificationDelivery(
            event_id=queued_event.id,
            channel_id=channel.id,
            success=success,
            status_code=status_code,
            response=response[:250] if response else None,
            error=error
        )
        db.add(delivery)
        if success: sent_count += 1
        
    if sent_count > 0:
        db.add(Event(device_id=device.id, event_type="alert_sent", message=f"Sent {sent_count} alerts"))
    else:
        db.add(Event(device_id=device.id, event_type="alert_failed", message="All alerts failed"))

def run_scan(db: Session, source: str = "scheduled") -> dict:
    logger.info("Starting UniFi scan (%s)", source)

    scan_start_event = Event(event_type="scan_started", severity="info", message=f"Scan started ({source})")
    db.add(scan_start_event)
    db.commit()

    client = UnifiClient()
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
    
    for c in clients_data:
        raw_mac = c.get("mac")
        mac = normalize_mac(raw_mac)
        if not mac: continue
            
        ip = c.get("ip")
        hostname = c.get("hostname")

        # Look up vendor in local OUI DB first, fallback to UniFi
        vendor = None
        if len(mac) >= 8:
            mac_prefix = mac[:8]
            oui_entry = db.query(OuiEntry).filter(OuiEntry.mac_prefix == mac_prefix).first()
            if oui_entry:
                vendor = oui_entry.vendor

        if not vendor:
            vendor = c.get("oui")

        site = c.get("site_id")
        ssid = c.get("essid")
        ap_mac = normalize_mac(c.get("ap_mac", ""))

        device = db.query(Device).filter(Device.mac == mac).first()
        is_new = False
        if not device:
            is_new = True
            device = Device(mac=mac, hostname=hostname, ip=ip, vendor=vendor, status="unknown", first_seen_at=now, last_seen_at=now, last_site=site, last_ssid=ssid, last_ap_mac=ap_mac)
            db.add(device)
            db.flush()
        else:
            device.last_seen_at = now
            if hostname: device.hostname = hostname
            if ip: device.ip = ip
            if vendor: device.vendor = vendor
            if site: device.last_site = site
            if ssid: device.last_ssid = ssid
            if ap_mac: device.last_ap_mac = ap_mac
            
        obs = Observation(device_id=device.id, mac=mac, ip=ip, hostname=hostname, site=site, ssid=ssid, ap_mac=ap_mac, raw_json=json.dumps(c), seen_at=now)
        db.add(obs)
        
        if is_new:
            db.add(Event(device_id=device.id, event_type="discovered", severity="info", message=f"New device discovered: {mac}"))
            
        # Trigger alerts check
        check_and_send_alerts(db, device, now)

    message = f"Scan finished ({source}). Processed {len(clients_data)} clients."
    db.add(Event(event_type="scan_finished", severity="info", message=message))
    db.commit()
    logger.info("Scan finished successfully")
    return {"success": True, "message": message, "devices_processed": len(clients_data)}
