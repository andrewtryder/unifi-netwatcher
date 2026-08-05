from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.schemas import BulkDeviceIdsRequest, NotesRequest, RenameRequest
from app.services.context import request_context_from_request
from app.services.devices import (
    DeviceActionError,
    DeviceNotFoundError,
    DeviceService,
    log_action,
)
from app.web.context import template_context
from app.web.templates_env import templates

router = APIRouter()

# Re-export for callers that historically imported log_action from this module
__all__ = ["router", "log_action", "get_device_or_404"]


def get_device_or_404(db: Session, device_id: int):
    from app.models import Device

    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


def _service(request: Request, db: Session) -> DeviceService:
    return DeviceService(db, request_context_from_request(request))


def _map_not_found(exc: DeviceNotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail="Device not found")


@router.post("/bulk/trust")
def bulk_trust(request: Request, req: BulkDeviceIdsRequest, db: Session = Depends(get_db)):
    updated = _service(request, db).bulk_trust(req.device_ids)
    db.commit()
    return {"status": "success", "updated": updated}


@router.post("/bulk/ignore")
def bulk_ignore(request: Request, req: BulkDeviceIdsRequest, db: Session = Depends(get_db)):
    updated = _service(request, db).bulk_ignore(req.device_ids)
    db.commit()
    return {"status": "success", "updated": updated}


@router.post("/{device_id}/trust")
def trust_device(request: Request, device_id: int, db: Session = Depends(get_db)):
    try:
        device = _service(request, db).trust(device_id)
    except DeviceNotFoundError as exc:
        raise _map_not_found(exc) from exc
    db.commit()
    return {"status": "success", "device_id": device.id}


@router.post("/{device_id}/ignore")
def ignore_device(request: Request, device_id: int, db: Session = Depends(get_db)):
    try:
        device = _service(request, db).ignore(device_id)
    except DeviceNotFoundError as exc:
        raise _map_not_found(exc) from exc
    db.commit()
    return {"status": "success", "device_id": device.id}


@router.post("/{device_id}/reset")
def reset_device(request: Request, device_id: int, db: Session = Depends(get_db)):
    try:
        device = _service(request, db).reset(device_id)
    except DeviceNotFoundError as exc:
        raise _map_not_found(exc) from exc
    db.commit()
    return {"status": "success", "device_id": device.id}


@router.post("/{device_id}/delete")
def delete_device(request: Request, device_id: int, db: Session = Depends(get_db)):
    try:
        deleted_id, mac = _service(request, db).delete(device_id)
    except DeviceNotFoundError as exc:
        raise _map_not_found(exc) from exc
    db.commit()
    return {"status": "success", "deleted_device_id": deleted_id, "mac": mac}


@router.post("/{device_id}/rename")
def rename_device(
    request: Request, device_id: int, req: RenameRequest, db: Session = Depends(get_db)
):
    try:
        device = _service(request, db).rename(device_id, req.display_name)
    except DeviceNotFoundError as exc:
        raise _map_not_found(exc) from exc
    db.commit()
    return {"status": "success", "device_id": device.id}


@router.post("/{device_id}/notes")
def update_device_notes(
    request: Request, device_id: int, req: NotesRequest, db: Session = Depends(get_db)
):
    try:
        device = _service(request, db).notes(device_id, req.notes)
    except DeviceNotFoundError as exc:
        raise _map_not_found(exc) from exc
    db.commit()
    return {"status": "success", "device_id": device.id}


@router.get("/htmx/{device_id}/block_modal")
def block_modal(request: Request, device_id: int, db: Session = Depends(get_db)):
    try:
        device = _service(request, db).get_or_raise(device_id)
    except DeviceNotFoundError as exc:
        raise _map_not_found(exc) from exc
    return templates.TemplateResponse(
        request=request,
        name="partials/block_modal.html",
        context={
            "request": request,
            "device": device,
            "dry_run_enabled": settings.UNIFI_DRY_RUN_BLOCKS,
        },
    )


@router.post("/htmx/{device_id}/block")
def block_device_action(request: Request, device_id: int, db: Session = Depends(get_db)):
    try:
        device = _service(request, db).block(device_id)
    except DeviceNotFoundError as exc:
        raise _map_not_found(exc) from exc
    except DeviceActionError:
        return HTMLResponse(
            "<div class='p-4 text-error text-sm'>Failed to execute block on controller.</div>",
            status_code=502,
        )
    db.commit()
    return templates.TemplateResponse(
        request=request,
        name="partials/block_success.html",
        context=template_context(db, request, device=device),
    )


@router.post("/htmx/{device_id}/unblock")
def unblock_device_action(request: Request, device_id: int, db: Session = Depends(get_db)):
    try:
        device = _service(request, db).unblock(device_id)
    except DeviceNotFoundError as exc:
        raise _map_not_found(exc) from exc
    except DeviceActionError:
        return HTMLResponse(
            "<div class='p-4 text-error text-sm'>Failed to execute unblock on controller.</div>",
            status_code=502,
        )
    db.commit()
    return templates.TemplateResponse(
        request=request,
        name="partials/block_success.html",
        context=template_context(db, request, device=device),
    )
