"""Hard-delete old observations and events based on retention settings."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.activity_log import record_event
from app.models import Event, NotificationDelivery, Observation
from app.web.display import resolve_event_retention, resolve_observation_retention

logger = logging.getLogger(__name__)


def run_retention(db: Session, *, now: datetime | None = None) -> dict:
    """Delete observations/events older than configured retention windows.

    ``0`` days disables pruning for that table. Notification deliveries for
    pruned events are removed first to satisfy the foreign key.
    """
    now = now or datetime.utcnow()
    obs_days, obs_source = resolve_observation_retention(db)
    event_days, event_source = resolve_event_retention(db)

    observations_deleted = 0
    events_deleted = 0
    deliveries_deleted = 0

    if obs_days > 0:
        cutoff = now - timedelta(days=obs_days)
        observations_deleted = (
            db.query(Observation)
            .filter(Observation.seen_at < cutoff)
            .delete(synchronize_session=False)
        )

    if event_days > 0:
        cutoff = now - timedelta(days=event_days)
        old_event_ids = [
            row[0] for row in db.query(Event.id).filter(Event.created_at < cutoff).all()
        ]
        if old_event_ids:
            deliveries_deleted = (
                db.query(NotificationDelivery)
                .filter(NotificationDelivery.event_id.in_(old_event_ids))
                .delete(synchronize_session=False)
            )
            events_deleted = (
                db.query(Event)
                .filter(Event.id.in_(old_event_ids))
                .delete(synchronize_session=False)
            )

    message = (
        f"Retention cleanup: deleted {observations_deleted} observations "
        f"(policy {obs_days}d/{obs_source}), {events_deleted} events "
        f"(policy {event_days}d/{event_source}), "
        f"{deliveries_deleted} notification deliveries"
    )
    record_event(
        db,
        "retention_run",
        message,
        metadata={
            "observations_deleted": observations_deleted,
            "events_deleted": events_deleted,
            "deliveries_deleted": deliveries_deleted,
            "observation_retention_days": obs_days,
            "event_retention_days": event_days,
        },
    )
    db.commit()
    logger.info(message)
    return {
        "observations_deleted": observations_deleted,
        "events_deleted": events_deleted,
        "deliveries_deleted": deliveries_deleted,
        "observation_retention_days": obs_days,
        "event_retention_days": event_days,
    }
