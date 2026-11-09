from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, fields
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
    """Declared capabilities supported by a UniFi adapter. Defaults to False for safety."""

    can_block: bool = False
    can_unblock: bool = False
    has_wifi_telemetry: bool = False
    has_switch_port: bool = False
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
class UniFiClientDevice(Mapping[str, Any]):
    """Normalized client device identity and telemetry conforming to collections.abc.Mapping."""

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

    @classmethod
    def from_official_dict(
        cls, data: dict[str, Any], site_id: str | None = None
    ) -> UniFiClientDevice:
        """Create a normalized UniFiClientDevice from an official UniFi API client dict."""
        from app.mac import normalize_mac

        raw_mac = str(data.get("macAddress") or data.get("mac") or "").strip()
        mac = normalize_mac(raw_mac) or raw_mac
        ip = data.get("ipAddress") or data.get("ip")
        name = data.get("name")
        hostname = data.get("hostname") or name

        # Connection type: "WIRED" -> True, "WIRELESS" -> False, or direct is_wired boolean
        client_type = data.get("type")
        is_wired: bool | None = None
        if isinstance(client_type, str):
            upper_type = client_type.strip().upper()
            if upper_type == "WIRED":
                is_wired = True
            elif upper_type in ("WIRELESS", "WIFI"):
                is_wired = False
        if is_wired is None:
            is_wired = as_bool(data.get("is_wired"))

        essid = data.get("ssid") or data.get("essid")
        ap_mac = data.get("accessPointMac") or data.get("ap_mac")
        rssi = data.get("rssi") or data.get("signal")
        tx_rate = data.get("txRateBps") or data.get("txRate") or data.get("tx_rate")
        rx_rate = data.get("rxRateBps") or data.get("rxRate") or data.get("rx_rate")
        sw_port = data.get("switchPort") or data.get("sw_port")

        return cls(
            mac=mac,
            ip=str(ip) if ip is not None else None,
            hostname=str(hostname) if hostname is not None else None,
            name=str(name) if name is not None else None,
            is_wired=is_wired,
            oui=data.get("oui"),
            site_id=str(data.get("siteId") or site_id or ""),
            essid=str(essid) if essid is not None else None,
            ap_mac=str(ap_mac) if ap_mac is not None else None,
            radio_proto=data.get("radioProto") or data.get("radio_proto"),
            channel=as_int(data.get("channel")),
            rssi=as_int(rssi),
            satisfaction=as_int(data.get("satisfaction")),
            tx_rate_bps=as_int(tx_rate),
            rx_rate_bps=as_int(rx_rate),
            tx_bytes_r=as_int(data.get("txBytesR") or data.get("tx_bytes-r")),
            rx_bytes_r=as_int(data.get("rxBytesR") or data.get("rx_bytes-r")),
            sw_port=as_int(sw_port),
            dev_cat=data.get("devCat") or data.get("dev_cat"),
            dev_family=data.get("devFamily") or data.get("dev_family"),
            dev_vendor=data.get("devVendor") or data.get("dev_vendor"),
            os_name=data.get("osName") or data.get("os_name"),
            raw_data=data,
        )

    def _keys(self) -> set[str]:
        keys = {f.name for f in fields(self) if f.name != "raw_data"}
        keys.update(self.raw_data.keys())
        keys.update({"signal", "tx_rate", "rx_rate", "tx_bytes-r", "rx_bytes-r"})
        return keys

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys())

    def __len__(self) -> int:
        return len(self._keys())

    def __getitem__(self, key: str) -> Any:
        if not isinstance(key, str):
            raise KeyError(key)

        # Legacy aliases
        if key == "signal":
            return self.rssi if self.rssi is not None else self.raw_data.get("signal")
        if key == "tx_rate":
            return (
                self.tx_rate_bps if self.tx_rate_bps is not None else self.raw_data.get("tx_rate")
            )
        if key == "rx_rate":
            return (
                self.rx_rate_bps if self.rx_rate_bps is not None else self.raw_data.get("rx_rate")
            )
        if key == "tx_bytes-r":
            return (
                self.tx_bytes_r if self.tx_bytes_r is not None else self.raw_data.get("tx_bytes-r")
            )
        if key == "rx_bytes-r":
            return (
                self.rx_bytes_r if self.rx_bytes_r is not None else self.raw_data.get("rx_bytes-r")
            )

        # Dataclass fields
        if hasattr(self, key) and key != "raw_data":
            val = getattr(self, key)
            if val is not None:
                return val

        # Check raw_data
        if key in self.raw_data:
            return self.raw_data[key]

        # Field exists on model but value is None
        if hasattr(self, key) and key != "raw_data":
            return None

        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        if key in {"signal", "tx_rate", "rx_rate", "tx_bytes-r", "rx_bytes-r"}:
            return True
        if hasattr(self, key) and key != "raw_data":
            return True
        return key in self.raw_data
