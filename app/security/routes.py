"""Security settings Web UI and mutation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.security.dependencies import require_same_origin
from app.security.middleware import effective_client_ip
from app.security.schemas import CidrPreviewResponse, HostsPreviewResponse
from app.security.service import (
    get_allowed_cidrs_list,
    get_allowed_hosts_list,
    get_security_settings,
    normalize_request_host,
    preview_cidrs,
    preview_hosts,
    public_security_flags,
    update_authentication,
    update_cidr,
    update_hosts,
)
from app.web.context import template_context
from app.web.templates_env import templates

router = APIRouter()


@router.get("", response_class=HTMLResponse)
def security_page(request: Request, db: Session = Depends(get_db)):
    flags = public_security_flags(db)
    settings = get_security_settings(db)
    raw_host = request.headers.get("host") or request.url.hostname or ""
    return templates.TemplateResponse(
        request=request,
        name="security.html",
        context=template_context(
            db,
            request,
            **flags,
            allowed_cidrs_list=get_allowed_cidrs_list(settings),
            allowed_hosts_list=get_allowed_hosts_list(settings),
            effective_client_ip=effective_client_ip(request),
            effective_request_host=normalize_request_host(raw_host),
        ),
    )


@router.post("/htmx/authentication", response_class=HTMLResponse)
def save_authentication(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_same_origin),
    authentication_enabled: str | None = Form(None),
    username: str = Form(""),
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    confirm_disable: str | None = Form(None),
):
    result = update_authentication(
        db,
        authentication_enabled=authentication_enabled in ("on", "true", "1"),
        username=username,
        current_password=current_password,
        new_password=new_password,
        confirm_password=confirm_password,
        confirm_disable=confirm_disable in ("on", "true", "1"),
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/security_message.html",
        context={
            "request": request,
            "ok": result.ok,
            "message": result.message,
            "errors": result.errors or [],
            "target_id": "auth-msg",
        },
        status_code=200 if result.ok else 400,
    )


@router.post("/htmx/cidr", response_class=HTMLResponse)
def save_cidr(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_same_origin),
    cidr_restriction_enabled: str | None = Form(None),
    allowed_cidrs: str = Form(""),
    allow_lockout: str | None = Form(None),
    lockout_confirmation: str = Form(""),
):
    result = update_cidr(
        db,
        cidr_restriction_enabled=cidr_restriction_enabled in ("on", "true", "1"),
        cidrs_text=allowed_cidrs,
        client_ip=effective_client_ip(request),
        allow_lockout=allow_lockout in ("on", "true", "1"),
        lockout_confirmation=lockout_confirmation,
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/security_message.html",
        context={
            "request": request,
            "ok": result.ok,
            "message": result.message,
            "errors": result.errors or [],
            "target_id": "cidr-msg",
        },
        status_code=200 if result.ok else 400,
    )


@router.post("/htmx/hosts", response_class=HTMLResponse)
def save_hosts(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_same_origin),
    host_restriction_enabled: str | None = Form(None),
    allowed_hosts: str = Form(""),
    allow_lockout: str | None = Form(None),
    lockout_confirmation: str = Form(""),
):
    raw_host = request.headers.get("host") or request.url.hostname or ""
    result = update_hosts(
        db,
        host_restriction_enabled=host_restriction_enabled in ("on", "true", "1"),
        hosts_text=allowed_hosts,
        request_host=raw_host,
        allow_lockout=allow_lockout in ("on", "true", "1"),
        lockout_confirmation=lockout_confirmation,
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/security_message.html",
        context={
            "request": request,
            "ok": result.ok,
            "message": result.message,
            "errors": result.errors or [],
            "target_id": "hosts-msg",
        },
        status_code=200 if result.ok else 400,
    )


@router.post("/api/cidr-preview", response_model=CidrPreviewResponse)
def cidr_preview(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_same_origin),
    allowed_cidrs: str = Form(""),
    cidr_restriction_enabled: str | None = Form(None),
):
    _ = db  # preview must not mutate settings
    payload = preview_cidrs(
        allowed_cidrs,
        client_ip=effective_client_ip(request),
        cidr_enabled=cidr_restriction_enabled in ("on", "true", "1"),
    )
    return CidrPreviewResponse(**payload)


@router.post("/api/hosts-preview", response_model=HostsPreviewResponse)
def hosts_preview(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_same_origin),
    allowed_hosts: str = Form(""),
    host_restriction_enabled: str | None = Form(None),
):
    _ = db
    raw_host = request.headers.get("host") or request.url.hostname or ""
    payload = preview_hosts(
        allowed_hosts,
        request_host=raw_host,
        host_enabled=host_restriction_enabled in ("on", "true", "1"),
    )
    return HostsPreviewResponse(**payload)
