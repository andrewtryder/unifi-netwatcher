from __future__ import annotations

from sqlalchemy.orm import Session

from app.activity_log import record_event
from app.config import settings
from app.db import utcnow
from app.models import AuditLog, Device, Event
from app.services.context import RequestContext, system_context
from app.unifi.client import get_unifi_client

_MAX_DETAIL_LEN = 200


class DeviceNotFoundError(Exception):
    pass


class DeviceActionError(Exception):
    pass


def _truncate(value: str | None, limit: int = _MAX_DETAIL_LEN) -> str | None:
    if value is None:
        return None
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _event_type_for_action(action: str) -> str:
    return {
        "trust": "trusted",
        "ignore": "ignored",
        "rename": "renamed",
        "notes": "note_added",
        "blocked": "blocked",
        "unblocked": "unblocked",
        "reset": "reset",
        "delete": "deleted",
    }.get(action, action)


def log_device_action(
    db: Session,
    device: Device,
    action: str,
    details: dict,
    ctx: RequestContext,
) -> None:
    safe_details = {
        **details,
        "client_ip": ctx.client_ip or None,
        "request_id": ctx.request_id,
        "result": details.get("result", "success"),
    }
    db.add(
        AuditLog(
            actor=ctx.actor,
            action=action,
            target_type="device",
            target_id=str(device.id),
            details_json=safe_details,
        )
    )
    db.add(
        Event(
            device_id=device.id,
            event_type=_event_type_for_action(action),
            severity="info",
            message=f"Device {device.mac} {action}",
            metadata_json={k: v for k, v in safe_details.items() if k != "result"},
        )
    )


class DeviceService:
    def __init__(self, db: Session, ctx: RequestContext):
        self.db = db
        self.ctx = ctx

    def get_or_raise(self, device_id: int) -> Device:
        device = self.db.query(Device).filter(Device.id == device_id).first()
        if not device:
            raise DeviceNotFoundError(f"Device {device_id} not found")
        return device

    def trust(self, device_id: int, *, bulk: bool = False) -> Device:
        device = self.get_or_raise(device_id)
        previous = device.status
        device.status = "trusted"
        device.updated_at = utcnow()
        log_device_action(
            self.db,
            device,
            "trust",
            {"previous_status": previous, "bulk": bulk},
            self.ctx,
        )
        return device

    def ignore(self, device_id: int, *, bulk: bool = False) -> Device:
        device = self.get_or_raise(device_id)
        previous = device.status
        device.status = "ignored"
        device.updated_at = utcnow()
        log_device_action(
            self.db,
            device,
            "ignore",
            {"previous_status": previous, "bulk": bulk},
            self.ctx,
        )
        return device

    def reset(self, device_id: int) -> Device:
        device = self.get_or_raise(device_id)
        previous = device.status
        device.status = "unknown"
        device.updated_at = utcnow()
        log_device_action(self.db, device, "reset", {"previous_status": previous}, self.ctx)
        return device

    def rename(self, device_id: int, display_name: str) -> Device:
        device = self.get_or_raise(device_id)
        old_name = device.display_name
        device.display_name = display_name
        device.updated_at = utcnow()
        log_device_action(
            self.db,
            device,
            "rename",
            {
                "old_name": _truncate(old_name),
                "new_name": _truncate(display_name),
            },
            self.ctx,
        )
        return device

    def notes(self, device_id: int, notes: str) -> Device:
        device = self.get_or_raise(device_id)
        device.notes = notes
        device.updated_at = utcnow()
        log_device_action(self.db, device, "notes", {"notes_length": len(notes)}, self.ctx)
        return device

    def delete(self, device_id: int) -> tuple[int, str]:
        device = self.get_or_raise(device_id)
        mac = device.mac
        status = device.status
        record_event(
            self.db,
            "deleted",
            f"Device deleted: {mac}",
            metadata={"mac": mac, "status": status, "device_id": device.id},
        )
        self.db.add(
            AuditLog(
                actor=self.ctx.actor,
                action="delete",
                target_type="device",
                target_id=str(device.id),
                details_json={
                    "mac": mac,
                    "status": status,
                    "client_ip": self.ctx.client_ip or None,
                    "request_id": self.ctx.request_id,
                    "result": "success",
                },
            )
        )
        self.db.delete(device)
        return device_id, mac

    def block(self, device_id: int) -> Device:
        device = self.get_or_raise(device_id)
        client = get_unifi_client()
        if not client.block_client(device.mac):
            raise DeviceActionError("Failed to execute block on controller")
        device.status = "blocked"
        device.updated_at = utcnow()
        log_device_action(
            self.db,
            device,
            "blocked",
            {"dry_run": settings.UNIFI_DRY_RUN_BLOCKS},
            self.ctx,
        )
        return device

    def unblock(self, device_id: int) -> Device:
        device = self.get_or_raise(device_id)
        client = get_unifi_client()
        if not client.unblock_client(device.mac):
            raise DeviceActionError("Failed to execute unblock on controller")
        device.status = "unknown"
        device.updated_at = utcnow()
        log_device_action(
            self.db,
            device,
            "unblocked",
            {"dry_run": settings.UNIFI_DRY_RUN_BLOCKS},
            self.ctx,
        )
        return device

    def bulk_trust(self, device_ids: list[int]) -> int:
        updated = 0
        for device_id in device_ids:
            device = self.db.query(Device).filter(Device.id == device_id).first()
            if not device:
                continue
            previous = device.status
            device.status = "trusted"
            device.updated_at = utcnow()
            log_device_action(
                self.db,
                device,
                "trust",
                {"previous_status": previous, "bulk": True},
                self.ctx,
            )
            updated += 1
        return updated

    def bulk_ignore(self, device_ids: list[int]) -> int:
        updated = 0
        for device_id in device_ids:
            device = self.db.query(Device).filter(Device.id == device_id).first()
            if not device:
                continue
            previous = device.status
            device.status = "ignored"
            device.updated_at = utcnow()
            log_device_action(
                self.db,
                device,
                "ignore",
                {"previous_status": previous, "bulk": True},
                self.ctx,
            )
            updated += 1
        return updated


def log_action(db: Session, device: Device, action: str, details: dict) -> None:
    """Back-compat for import paths; attributes action to system."""
    log_device_action(db, device, action, details, system_context())
