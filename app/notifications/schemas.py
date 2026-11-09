from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


class WebhookMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"


class WebhookConfig(BaseModel):
    type: Literal["webhook"] = "webhook"
    url: HttpUrl
    method: WebhookMethod = WebhookMethod.POST
    headers: dict[str, str] = Field(default_factory=dict)
    body_template: str = Field(default='{"text": "{{message}}"}', max_length=8000)
    query_param: str = Field(default="text", max_length=64)
    allow_private_network: bool = False

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("Webhook URL must use HTTPS")
        return value

    @field_validator("headers")
    @classmethod
    def _header_limits(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 20:
            raise ValueError("At most 20 headers allowed")
        for key, val in value.items():
            if len(key) > 128 or len(val) > 1024:
                raise ValueError("Header name/value too long")
            if "\n" in key or "\r" in key or "\n" in val or "\r" in val:
                raise ValueError("Headers must not contain newlines")
        return value

    @field_validator("body_template")
    @classmethod
    def _template_is_json_object(cls, value: str) -> str:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("body_template must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("body_template must be a JSON object")
        return value


class PushoverConfig(BaseModel):
    type: Literal["pushover"] = "pushover"
    token: Annotated[str, Field(min_length=8, max_length=128)]
    user: Annotated[str, Field(min_length=8, max_length=128)]

    @field_validator("token", "user", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class AppriseConfig(BaseModel):
    type: Literal["apprise"] = "apprise"
    url: Annotated[str, Field(min_length=3, max_length=2048)]
    allow_private_network: bool = False

    @field_validator("url")
    @classmethod
    def _validate_apprise_url(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Apprise URL is required")
        if clean.lower().startswith(("file:", "file/")):
            raise ValueError("File schemes are not permitted")
        if "://" not in clean:
            raise ValueError("Apprise URL must include a scheme (e.g. gotifys://, discord://)")
        return clean


NotificationConfig = Annotated[
    WebhookConfig | PushoverConfig | AppriseConfig,
    Field(discriminator="type"),
]


def parse_notification_config(channel_type: str, raw: dict[str, Any] | str) -> dict[str, Any]:
    """Validate and return a normalized JSON-serializable config dict."""
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("config_json must be valid JSON") from exc
    else:
        payload = dict(raw)
    payload = {**payload, "type": channel_type}
    if channel_type == "webhook":
        model = WebhookConfig.model_validate(payload)
    elif channel_type == "pushover":
        model = PushoverConfig.model_validate(payload)
    elif channel_type == "apprise":
        model = AppriseConfig.model_validate(payload)
    else:
        raise ValueError(f"Unsupported notification type: {channel_type}")
    data = model.model_dump(mode="json")
    data.pop("type", None)
    return data
