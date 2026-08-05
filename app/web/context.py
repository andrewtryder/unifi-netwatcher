from sqlalchemy.orm import Session

from app.models import Device
from app.security.service import public_security_flags


def nav_context(db: Session) -> dict:
    flags = public_security_flags(db)
    return {
        "unknown_count": db.query(Device).filter(Device.status == "unknown").count(),
        **flags,
    }


def template_context(db: Session, request, **kwargs) -> dict:
    return {"request": request, **nav_context(db), **kwargs}
