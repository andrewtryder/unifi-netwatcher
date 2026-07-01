from datetime import datetime, timezone
from typing import Optional


def format_relative_ago(dt: Optional[datetime], *, parens: bool = False) -> str:
    if dt is None:
        return ""

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    now = datetime.now(timezone.utc)
    seconds = max(0, int((now - dt).total_seconds()))

    if seconds < 60:
        text = f"{seconds}s ago"
    else:
        minutes = seconds // 60
        if minutes < 60:
            text = f"{minutes}m ago"
        else:
            hours = minutes // 60
            rem_min = minutes % 60
            if hours < 24:
                text = f"{hours}h {rem_min}m ago" if rem_min else f"{hours}h ago"
            else:
                days = hours // 24
                text = f"{days}d ago"

    return f"({text})" if parens else text
