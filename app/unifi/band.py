"""Map UniFi radio/channel fields to a human-readable band label."""

from __future__ import annotations


def band_label(
    *,
    is_wired: bool | None = None,
    radio_proto: str | None = None,
    channel: int | None = None,
) -> str:
    """Return Wired / 2.4 GHz / 5 GHz / 6 GHz / Wi-Fi from client telemetry."""
    if is_wired:
        return "Wired"

    proto = str(radio_proto).lower() if radio_proto else ""
    # 6 GHz radios often report low PSC channel numbers — prefer proto over channel.
    if "6e" in proto or "6g" in proto or proto in {"be", "ax6"}:
        return "6 GHz"

    if channel is not None:
        try:
            ch = int(channel)
        except TypeError, ValueError:
            ch = None
        if ch is not None:
            if 1 <= ch <= 14:
                return "2.4 GHz"
            # 5 GHz UNII channels typically 36–177
            if 36 <= ch <= 177:
                return "5 GHz"
            if ch > 177:
                return "6 GHz"
            if 15 <= ch <= 35:
                return "6 GHz"

    if proto:
        if proto in {"ac", "ax", "a", "n", "na"}:
            return "5 GHz"
        if proto in {"ng", "b", "g", "n2"}:
            return "2.4 GHz"
        return str(radio_proto)

    return "Wi-Fi"


def connection_mix_key(
    *,
    is_wired: bool | None = None,
    radio_proto: str | None = None,
    channel: int | None = None,
) -> str:
    """Bucket key for connection-mix charts: wired | band_24 | band_5 | band_6 | other."""
    label = band_label(is_wired=is_wired, radio_proto=radio_proto, channel=channel)
    if label == "Wired":
        return "wired"
    if label == "2.4 GHz":
        return "band_24"
    if label == "5 GHz":
        return "band_5"
    if label == "6 GHz":
        return "band_6"
    return "other"
