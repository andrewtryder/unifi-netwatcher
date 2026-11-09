from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class UniFiError(Exception):
    """Base exception for all UniFi controller and API interactions."""


class UniFiAuthError(UniFiError):
    """Authentication failed (invalid credentials, expired token, 401)."""


class UniFiPermissionError(UniFiError):
    """Insufficient permissions to perform the requested operation (403)."""


class UniFiConnectionError(UniFiError):
    """Network connection failure, DNS resolution failure, or unreachable host."""


class UniFiTimeoutError(UniFiError):
    """Request or connect timeout contacting the UniFi controller."""


class UniFiResponseError(UniFiError):
    """Malformed response, server error (5xx), or invalid payload structure."""


@dataclass(frozen=True)
class UniFiCapabilities:
    """Declared capabilities supported by a UniFi adapter."""

    can_block: bool = True
    can_unblock: bool = True
    has_wifi_telemetry: bool = True
    has_switch_port: bool = True
    supports_pagination: bool = False


def as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes"}:
            return True
        if lowered in {"0", "false", "no"}:
            return False
    return None


@dataclass
class UniFiClientDevice:
    """Normalized client device identity and telemetry across UniFi APIs."""

    mac: str
    ip: str | None = None
    hostname: str | None = None
    name: str | None = None
    is_wired: bool | None = None
    oui: str | None = None
    site_id: str | None = None
    essid: str | None = None
    ap_mac: str | None = None
    radio_proto: str | None = None
    channel: int | None = None
    rssi: int | None = None
    satisfaction: int | None = None
    tx_rate_bps: int | None = None
    rx_rate_bps: int | None = None
    tx_bytes_r: int | None = None
    rx_bytes_r: int | None = None
    sw_port: int | None = None
    dev_cat: str | None = None
    dev_family: str | None = None
    dev_vendor: str | None = None
    os_name: str | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_legacy_dict(cls, data: dict[str, Any]) -> UniFiClientDevice:
        """Create a normalized UniFiClientDevice from a legacy UniFi client dict."""
        rssi = data.get("rssi")
        if rssi is None:
            rssi = data.get("signal")

        return cls(
            mac=str(data.get("mac", "")),
            ip=data.get("ip"),
            hostname=data.get("hostname"),
            name=data.get("name"),
            is_wired=as_bool(data.get("is_wired")),
            oui=data.get("oui"),
            site_id=data.get("site_id"),
            essid=data.get("essid"),
            ap_mac=data.get("ap_mac"),
            radio_proto=data.get("radio_proto"),
            channel=as_int(data.get("channel")),
            rssi=as_int(rssi),
            satisfaction=as_int(data.get("satisfaction")),
            tx_rate_bps=as_int(data.get("tx_rate")),
            rx_rate_bps=as_int(data.get("rx_rate")),
            tx_bytes_r=as_int(data.get("tx_bytes-r")),
            rx_bytes_r=as_int(data.get("rx_bytes-r")),
            sw_port=as_int(data.get("sw_port")),
            dev_cat=data.get("dev_cat"),
            dev_family=data.get("dev_family"),
            dev_vendor=data.get("dev_vendor"),
            os_name=data.get("os_name"),
            raw_data=data,
        )

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like access for backward compatibility with existing consumers."""
        if hasattr(self, key):
            val = getattr(self, key)
            if val is not None:
                return val
        if key == "signal" and self.rssi is not None:
            return self.rssi
        if key == "tx_rate" and self.tx_rate_bps is not None:
            return self.tx_rate_bps
        if key == "rx_rate" and self.rx_rate_bps is not None:
            return self.rx_rate_bps
        if key == "tx_bytes-r" and self.tx_bytes_r is not None:
            return self.tx_bytes_r
        if key == "rx_bytes-r" and self.rx_bytes_r is not None:
            return self.rx_bytes_r
        return self.raw_data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None and key not in self.raw_data and not hasattr(self, key):
            raise KeyError(key)
        return val

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key) or key in self.raw_data
