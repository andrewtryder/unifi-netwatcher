from datetime import UTC, datetime


def format_relative_ago(dt: datetime | None, *, parens: bool = False) -> str:
    if dt is None:
        return ""

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)

    now = datetime.now(UTC)
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
