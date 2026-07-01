from sqlalchemy.orm import Session

from app.models import Device


def nav_context(db: Session) -> dict:
    return {
        "unknown_count": db.query(Device).filter(Device.status == "unknown").count(),
    }


def template_context(db: Session, request, **kwargs) -> dict:
    return {"request": request, **nav_context(db), **kwargs}
