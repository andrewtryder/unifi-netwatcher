from __future__ import annotations

import json
import logging
from typing import Any

from app.notifications.base import NotificationProvider
from app.notifications.http import (
    ResponseBodyLimitExceeded,
    ResponseReadDeadlineExceeded,
    get_notification_http_client,
    read_capped_response_text,
)
from app.notifications.ssrf import WebhookURLError, pinned_request_url, resolve_webhook_target

logger = logging.getLogger(__name__)

DEFAULT_BODY_TEMPLATE = '{"text": "{{message}}"}'
PLACEHOLDER = "{{message}}"
ALLOWED_METHODS = frozenset({"GET", "POST", "PUT"})
MAX_JSON_BODY_BYTES = 64 * 1024


def _replace_placeholders(node: Any, message: str) -> Any:
    if isinstance(node, str):
        return node.replace(PLACEHOLDER, message)
    if isinstance(node, list):
        return [_replace_placeholders(item, message) for item in node]
    if isinstance(node, dict):
        return {key: _replace_placeholders(value, message) for key, value in node.items()}
    return node


def _sanitize_headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, val in list(headers.items())[:20]:
        key_s = str(key)
        val_s = str(val)
        if len(key_s) > 128 or len(val_s) > 1024:
            continue
        if any(ch in key_s for ch in "\r\n") or any(ch in val_s for ch in "\r\n"):
            continue
        cleaned[key_s] = val_s
    return cleaned


class WebhookProvider(NotificationProvider):
    type = "webhook"

    def validate_config(self, config: dict) -> bool:
        try:
            url = config.get("url")
            if not url:
                return False
            allow_private = bool(config.get("allow_private_network", False))
            resolve_webhook_target(str(url), allow_private_network=allow_private)
            template = config.get("body_template") or DEFAULT_BODY_TEMPLATE
            parsed = json.loads(template)
            return isinstance(parsed, dict)
        except WebhookURLError, json.JSONDecodeError, TypeError, ValueError:
            return False

    def _build_payload(self, message: str, config: dict) -> dict:
        template = config.get("body_template") or DEFAULT_BODY_TEMPLATE
        parsed = json.loads(template)
        if not isinstance(parsed, dict):
            raise ValueError("body_template must be a JSON object")
        result = _replace_placeholders(parsed, message)
        if not isinstance(result, dict):
            raise ValueError("body_template must produce a JSON object")
        return result

    def send(self, message: str, config: dict) -> tuple[bool, int, str, str]:
        url = config.get("url")
        if not url:
            return False, 0, "", "Missing webhook URL"
        allow_private = bool(config.get("allow_private_network", False))
        try:
            # Resolve once and pin to validated IP for the request (closes DNS TOCTOU).
            target = resolve_webhook_target(str(url), allow_private_network=allow_private)
        except WebhookURLError as exc:
            return False, 0, "", str(exc)

        method = str(config.get("method", "POST")).upper()
        if method not in ALLOWED_METHODS:
            return False, 0, "", f"Unsupported method: {method}"
        headers = _sanitize_headers(config.get("headers") or {})
        headers["Host"] = target.hostname
        client = get_notification_http_client()
        request_url = pinned_request_url(target, original_url=str(url))
        extensions = {"sni_hostname": target.hostname}

        try:
            if method == "GET":
                param_name = str(config.get("query_param", "text"))[:64] or "text"
                with client.stream(
                    "GET",
                    request_url,
                    params={param_name: message},
                    headers=headers,
                    extensions=extensions,
                ) as r:
                    body = read_capped_response_text(r)
                    success = r.status_code in (200, 201, 202, 204)
                    return success, r.status_code, body, ""
            try:
                payload = self._build_payload(message, config)
            except (json.JSONDecodeError, ValueError) as e:
                return False, 0, "", f"Invalid body template JSON: {e}"
            encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_JSON_BODY_BYTES:
                return False, 0, "", "Request body exceeds size limit"
            with client.stream(
                method,
                request_url,
                content=encoded,
                headers={**headers, "Content-Type": "application/json"},
                extensions=extensions,
            ) as r:
                body = read_capped_response_text(r)
                success = r.status_code in (200, 201, 202, 204)
                return success, r.status_code, body, ""
        except (ResponseBodyLimitExceeded, ResponseReadDeadlineExceeded) as e:
            logger.error("Webhook response aborted: %s", e)
            return False, 0, "", str(e)
        except Exception as e:
            logger.error(f"Webhook send error: {e}")
            return False, 0, "", str(e)
