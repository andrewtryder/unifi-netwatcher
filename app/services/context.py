from __future__ import annotations

import uuid
from dataclasses import dataclass

from starlette.requests import Request


@dataclass(frozen=True)
class RequestContext:
    actor: str
    client_ip: str
    request_id: str


def request_context_from_request(request: Request) -> RequestContext:
    actor = getattr(request.state, "actor", None) or "anonymous"
    client_ip = getattr(request.state, "client_ip", None) or ""
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    return RequestContext(actor=actor, client_ip=client_ip, request_id=request_id)


def system_context() -> RequestContext:
    return RequestContext(actor="system", client_ip="", request_id=str(uuid.uuid4()))
