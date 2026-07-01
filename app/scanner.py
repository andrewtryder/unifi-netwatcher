import json
import logging
from sqlalchemy.orm import Session
from datetime import datetime

from app.models import Device, Observation, Event
from app.unifi.client import UnifiClient
from app.mac import normalize_mac

logger = logging.getLogger(__name__)

def run_scan(db: Session):
    logger.info("Starting UniFi scan")
    
    # Create scan started event
    scan_start_event = Event(event_type="scan_started", severity="info", message="Scan started")
    db.add(scan_start_event)
    db.commit()

    client = UnifiClient()
    clients_data = client.get_clients()
    
    if not clients_data and not client.mock_mode:
        # We might want to log a failure if we expect data and aren't in mock mode, 
        # but let's just log a finish event for now.
        scan_fail_event = Event(event_type="scan_failed", severity="error", message="Failed to fetch clients or zero clients returned")
        db.add(scan_fail_event)
        db.commit()
        return

    now = datetime.utcnow()
    
    for c in clients_data:
        raw_mac = c.get("mac")
        mac = normalize_mac(raw_mac)
        if not mac:
            continue
            
        ip = c.get("ip")
        hostname = c.get("hostname")
        vendor = c.get("oui")
        site = c.get("site_id")
        ssid = c.get("essid")
        ap_mac = normalize_mac(c.get("ap_mac", ""))

        # 1. Upsert Device
        device = db.query(Device).filter(Device.mac == mac).first()
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
                last_ap_mac=ap_mac
            )
            db.add(device)
            db.flush() # flush to get device.id
        else:
            device.last_seen_at = now
            if hostname: device.hostname = hostname
            if ip: device.ip = ip
            if vendor: device.vendor = vendor
            if site: device.last_site = site
            if ssid: device.last_ssid = ssid
            if ap_mac: device.last_ap_mac = ap_mac
            
        # 2. Add Observation
        obs = Observation(
            device_id=device.id,
            mac=mac,
            ip=ip,
            hostname=hostname,
            site=site,
            ssid=ssid,
            ap_mac=ap_mac,
            raw_json=json.dumps(c),
            seen_at=now
        )
        db.add(obs)
        
        # 3. Handle Events
        if is_new:
            event = Event(
                device_id=device.id,
                event_type="discovered",
                severity="info",
                message=f"New device discovered: {mac}"
            )
            db.add(event)
            
        # TODO: milestone 4 -> alerts logic here

    # Finish
    scan_finish_event = Event(event_type="scan_finished", severity="info", message=f"Scan finished. Processed {len(clients_data)} clients.")
    db.add(scan_finish_event)
    db.commit()
    logger.info("Scan finished successfully")

