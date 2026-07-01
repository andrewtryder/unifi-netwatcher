import json
from typing import Optional
from sqlalchemy.orm import Session

from app.models import Event


def record_event(
    db: Session,
    event_type: str,
    message: str,
    *,
    severity: str = "info",
    device_id: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> None:
    db.add(Event(
        device_id=device_id,
        event_type=event_type,
        severity=severity,
        message=message,
        metadata_json=json.dumps(metadata) if metadata else None,
    ))
