from datetime import datetime, timezone, timedelta

from app.web.formatting import format_relative_ago


def test_format_relative_ago_seconds():
    dt = datetime.now(timezone.utc) - timedelta(seconds=30)
    assert format_relative_ago(dt) == "30s ago"


def test_format_relative_ago_with_parens():
    dt = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert format_relative_ago(dt, parens=True) == "(5m ago)"
