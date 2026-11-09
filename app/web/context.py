from sqlalchemy.orm import Session

from app.security.service import public_security_flags
from app.unifi.status import get_unifi_connection_status
from app.web.pagination import device_status_counts


def nav_context(db: Session, *, counts: dict[str, int] | None = None) -> dict:
    flags = public_security_flags(db)
    status_counts = counts if counts is not None else device_status_counts(db)
    return {
        "unknown_count": status_counts.get("unknown", 0),
        "device_counts": status_counts,
        **flags,
    }


def template_context(db: Session, request, **kwargs) -> dict:
    counts = kwargs.pop("device_counts", None)
    nonce = getattr(getattr(request, "state", None), "csp_nonce", "") or ""
    unifi_status = kwargs.pop("unifi_status", None) or get_unifi_connection_status(db)
    return {
        "request": request,
        "csp_nonce": nonce,
        "unifi_status": unifi_status,
        "adapter_capabilities": unifi_status.capabilities,
        **nav_context(db, counts=counts),
        **kwargs,
    }
