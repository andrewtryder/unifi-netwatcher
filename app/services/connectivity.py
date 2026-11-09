"""Device connectivity state machine and behavioral event engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.config import settings
from app.models import ConnectivityState, Device

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConnectivityTransition:
    """Represents a state transition evaluated by the connectivity state machine."""

    device_id: int
    mac: str
    previous_state: str
    new_state: str
    event_type: str | None
    event_message: str | None
    event_metadata: dict[str, Any] | None


@dataclass(frozen=True)
class NetworkTransition:
    """Represents an SSID, AP, or IP change for an active online device."""

    device_id: int
    mac: str
    event_type: str
    event_message: str
    event_metadata: dict[str, Any]


def resolve_offline_thresholds(db: Any | None = None) -> tuple[int, int]:
    """Resolve offline detection thresholds, checking database Setting overrides first."""
    missed_scans = settings.DEVICE_OFFLINE_MISSED_SCANS
    grace_seconds = settings.DEVICE_OFFLINE_GRACE_SECONDS
    if db is not None:
        try:
            from app.models import Setting

            s_missed = (
                db.query(Setting).filter(Setting.key == "device_offline_missed_scans").first()
            )
            if s_missed and s_missed.value and s_missed.value.strip().isdigit():
                val = int(s_missed.value.strip())
                if val >= 2:
                    missed_scans = val

            s_grace = (
                db.query(Setting).filter(Setting.key == "device_offline_grace_seconds").first()
            )
            if s_grace and s_grace.value and s_grace.value.strip().isdigit():
                val = int(s_grace.value.strip())
                if val >= 0:
                    grace_seconds = val
        except Exception:
            pass
    return missed_scans, grace_seconds


class ConnectivityService:
    """Evaluates and transitions device connectivity state independently of approval status."""

    def __init__(
        self,
        missed_scans_threshold: int | None = None,
        grace_seconds: int | None = None,
    ) -> None:
        self.missed_scans_threshold = (
            missed_scans_threshold
            if missed_scans_threshold is not None
            else settings.DEVICE_OFFLINE_MISSED_SCANS
        )
        self.grace_seconds = (
            grace_seconds if grace_seconds is not None else settings.DEVICE_OFFLINE_GRACE_SECONDS
        )

    def handle_discovered_device(
        self, device: Device, now: datetime
    ) -> ConnectivityTransition | None:
        """Rule A: Genuinely new device discovered during successful scan.

        Initializes connectivity to online, establishes connection baseline,
        and resets absence tracking without producing a redundant connection event.
        """
        device.connectivity_state = ConnectivityState.ONLINE.value
        device.last_connected_at = now
        device.consecutive_missed_scans = 0
        device.offline_candidate_since = None
        return None

    def handle_observed_device(
        self, device: Device, now: datetime
    ) -> ConnectivityTransition | None:
        """Rules B, E, & F: Known device observed in successful complete scan.

        - If previously offline (Rule E): transitions to online and generates
          a 'device_reconnected' event.
        - If previously unknown (Rule F): initializes baseline to online without
          generating artificial reconnection notifications.
        - If already online (Rule B): retains online state and clears any pending
          absence counters.
        """
        prev_state = device.connectivity_state or ConnectivityState.UNKNOWN.value

        device.connectivity_state = ConnectivityState.ONLINE.value
        device.consecutive_missed_scans = 0
        device.offline_candidate_since = None

        if prev_state == ConnectivityState.OFFLINE.value:
            # Rule E — Reconnection
            offline_duration_seconds: float | None = None
            if device.last_disconnected_at:
                last_disconnected = (
                    device.last_disconnected_at
                    if device.last_disconnected_at.tzinfo is not None
                    else device.last_disconnected_at.replace(tzinfo=now.tzinfo)
                )
                offline_duration_seconds = max(0.0, (now - last_disconnected).total_seconds())

            device.last_connected_at = now
            display_name = device.display_name or device.hostname or device.name or device.mac
            msg = f"Device reconnected: {display_name} ({device.mac})"
            meta = {
                "previous_state": prev_state,
                "new_state": ConnectivityState.ONLINE.value,
                "mac": device.mac,
                "ip": device.ip,
                "hostname": device.hostname,
                "offline_duration_seconds": offline_duration_seconds,
                "last_disconnected_at": (
                    device.last_disconnected_at.isoformat() if device.last_disconnected_at else None
                ),
            }
            logger.info("Device reconnected: %s (%s)", device.mac, display_name)
            return ConnectivityTransition(
                device_id=device.id,
                mac=device.mac,
                previous_state=prev_state,
                new_state=ConnectivityState.ONLINE.value,
                event_type="device_reconnected",
                event_message=msg,
                event_metadata=meta,
            )

        if prev_state == ConnectivityState.UNKNOWN.value:
            # Rule F — Baseline initialization
            device.last_connected_at = now
            logger.debug("Device %s established initial online baseline.", device.mac)
            return None

        # Rule B — Device remains online
        return None

    def detect_network_transitions(
        self,
        device: Device,
        observed_ip: str | None,
        observed_ssid: str | None,
        observed_ap_mac: str | None,
        now: datetime,
    ) -> list[NetworkTransition]:
        """Detect SSID, access point roaming, or IP address changes for an already online device.

        Safeguards:
        - Only evaluated if device is already confirmed ONLINE (avoids baseline & reconnection flapping).
        - Ignores None/empty/missing metadata (avoids flapping from transient UniFi omissions).
        - Preserves normalized comparison for AP MACs.
        - Returns list of transitions to record atomically with the scan transaction.
        """
        transitions: list[NetworkTransition] = []
        if (device.connectivity_state or "") != ConnectivityState.ONLINE.value:
            return transitions

        display_name = device.display_name or device.hostname or device.name or device.mac

        # 1. SSID transition
        if device.last_ssid and observed_ssid:
            old_ssid = device.last_ssid.strip()
            new_ssid = observed_ssid.strip()
            if old_ssid and new_ssid and old_ssid != new_ssid:
                msg = f"Device SSID changed: {display_name} ({old_ssid} -> {new_ssid})"
                meta = {
                    "old_ssid": old_ssid,
                    "new_ssid": new_ssid,
                    "mac": device.mac,
                    "ip": observed_ip or device.ip,
                }
                logger.info("Device %s SSID changed: %s -> %s", device.mac, old_ssid, new_ssid)
                transitions.append(
                    NetworkTransition(
                        device_id=device.id,
                        mac=device.mac,
                        event_type="device_ssid_changed",
                        event_message=msg,
                        event_metadata=meta,
                    )
                )

        # 2. Access Point roaming transition
        if device.last_ap_mac and observed_ap_mac:
            from app.mac import normalize_mac

            old_ap = normalize_mac(device.last_ap_mac)
            new_ap = normalize_mac(observed_ap_mac)
            if old_ap and new_ap and old_ap != new_ap:
                msg = f"Device roamed to AP: {display_name} ({old_ap} -> {new_ap})"
                meta = {
                    "old_ap_mac": old_ap,
                    "new_ap_mac": new_ap,
                    "mac": device.mac,
                    "ssid": observed_ssid or device.last_ssid,
                }
                logger.info("Device %s roamed AP: %s -> %s", device.mac, old_ap, new_ap)
                transitions.append(
                    NetworkTransition(
                        device_id=device.id,
                        mac=device.mac,
                        event_type="device_ap_changed",
                        event_message=msg,
                        event_metadata=meta,
                    )
                )

        # 3. IP address transition
        if device.ip and observed_ip:
            old_ip = device.ip.strip()
            new_ip = observed_ip.strip()
            if old_ip and new_ip and old_ip != new_ip:
                msg = f"Device IP changed: {display_name} ({old_ip} -> {new_ip})"
                meta = {
                    "old_ip": old_ip,
                    "new_ip": new_ip,
                    "mac": device.mac,
                }
                logger.info("Device %s IP changed: %s -> %s", device.mac, old_ip, new_ip)
                transitions.append(
                    NetworkTransition(
                        device_id=device.id,
                        mac=device.mac,
                        event_type="device_network_changed",
                        event_message=msg,
                        event_metadata=meta,
                    )
                )

        return transitions

    def handle_absent_device(self, device: Device, now: datetime) -> ConnectivityTransition | None:
        """Rules C, D, & F: Device absent from a successful complete scan.

        - If unknown baseline (Rule F): remains unknown until first seen online.
        - If already offline (Rule D): remains offline without repeated events.
        - If online (Rule C): increments missed-scan counter and sets candidate time.
        - Confirmed offline (Rule D): if both missed scans threshold AND grace period
          are met, transitions to offline and generates 'device_disconnected'.
        """
        current_state = device.connectivity_state or ConnectivityState.UNKNOWN.value

        # Rule F: Devices without confirmed online baseline remain unknown
        if current_state == ConnectivityState.UNKNOWN.value:
            if device.consecutive_missed_scans is None:
                device.consecutive_missed_scans = 0
            return None

        # Rule D: Already offline devices remain offline (no counter increments)
        if current_state == ConnectivityState.OFFLINE.value:
            return None

        # Rule C: Device was online, now absent
        device.consecutive_missed_scans = (device.consecutive_missed_scans or 0) + 1
        if device.offline_candidate_since is None:
            device.offline_candidate_since = now

        # Rule D: Check confirmation conditions
        candidate_time = (
            device.offline_candidate_since
            if device.offline_candidate_since.tzinfo is not None
            else device.offline_candidate_since.replace(tzinfo=now.tzinfo)
        )
        elapsed_seconds = (now - candidate_time).total_seconds()

        is_confirmed = (
            device.consecutive_missed_scans >= self.missed_scans_threshold
            and elapsed_seconds >= self.grace_seconds
        )

        if is_confirmed:
            device.connectivity_state = ConnectivityState.OFFLINE.value
            device.last_disconnected_at = now
            display_name = device.display_name or device.hostname or device.name or device.mac
            msg = f"Device disconnected: {display_name} ({device.mac})"

            online_duration_seconds: float | None = None
            if device.last_connected_at:
                last_connected = (
                    device.last_connected_at
                    if device.last_connected_at.tzinfo is not None
                    else device.last_connected_at.replace(tzinfo=now.tzinfo)
                )
                online_duration_seconds = max(0.0, (now - last_connected).total_seconds())

            meta = {
                "previous_state": ConnectivityState.ONLINE.value,
                "new_state": ConnectivityState.OFFLINE.value,
                "mac": device.mac,
                "ip": device.ip,
                "hostname": device.hostname,
                "consecutive_missed_scans": device.consecutive_missed_scans,
                "online_duration_seconds": online_duration_seconds,
                "last_connected_at": (
                    device.last_connected_at.isoformat() if device.last_connected_at else None
                ),
                "last_seen_at": (device.last_seen_at.isoformat() if device.last_seen_at else None),
                "offline_candidate_since": device.offline_candidate_since.isoformat(),
            }
            logger.info("Device disconnected: %s (%s)", device.mac, display_name)
            return ConnectivityTransition(
                device_id=device.id,
                mac=device.mac,
                previous_state=ConnectivityState.ONLINE.value,
                new_state=ConnectivityState.OFFLINE.value,
                event_type="device_disconnected",
                event_message=msg,
                event_metadata=meta,
            )

        return None
