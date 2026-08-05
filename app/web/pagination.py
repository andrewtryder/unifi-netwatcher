from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, func
from sqlalchemy.orm import Query, Session

from app.models import Device

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


@dataclass(frozen=True)
class Page:
    items: list
    page: int
    page_size: int
    total: int

    @property
    def total_pages(self) -> int:
        if self.page_size <= 0:
            return 0
        return max(1, (self.total + self.page_size - 1) // self.page_size)

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages


def clamp_pagination(page: int | None, page_size: int | None) -> tuple[int, int]:
    p = 1 if page is None or page < 1 else page
    size = DEFAULT_PAGE_SIZE if page_size is None else page_size
    size = max(1, min(size, MAX_PAGE_SIZE))
    return p, size


def paginate(query: Query, page: int, page_size: int) -> Page:
    total = query.order_by(None).count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return Page(items=items, page=page, page_size=page_size, total=total)


def device_status_counts(db: Session) -> dict[str, int]:
    """Single aggregate for dashboard/nav device status counts."""
    row = db.query(
        func.count(Device.id).label("total"),
        func.sum(case((Device.status == "unknown", 1), else_=0)).label("unknown"),
        func.sum(case((Device.status == "trusted", 1), else_=0)).label("trusted"),
        func.sum(case((Device.status == "ignored", 1), else_=0)).label("ignored"),
    ).one()
    return {
        "total": int(row.total or 0),
        "unknown": int(row.unknown or 0),
        "trusted": int(row.trusted or 0),
        "ignored": int(row.ignored or 0),
    }
