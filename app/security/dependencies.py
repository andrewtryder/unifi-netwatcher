"""Optional FastAPI dependencies for security routes."""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.security.middleware import origin_matches_request


def require_same_origin(request: Request) -> None:
    """Reject mutating requests with a mismatched Origin header."""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not origin_matches_request(request):
        raise HTTPException(status_code=403, detail="Origin mismatch")
