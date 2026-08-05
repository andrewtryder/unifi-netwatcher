from datetime import datetime

from app.models import Device
from app.scanner import _apply_telemetry, _client_telemetry
from app.web.display import (
    bytes_r_to_mbps,
    device_display_name,
    device_fingerprint_subtitle,
    device_icon,
)


def test_client_telemetry_hyphenated_keys_and_rssi_fallback():
    raw = {
        "name": "Office Phone",
        "is_wired": False,
        "radio_proto": "ax",
        "channel": 36,
        "signal": -70,
        "satisfaction": 80,
        "tx_rate": 1000,
        "rx_rate": 2000,
        "tx_bytes-r": 3000,
        "rx_bytes-r": 4000,
        "dev_cat": "phone",
        "dev_family": "smartphone",
        "dev_vendor": "Apple",
        "os_name": "iOS",
    }
    tel = _client_telemetry(raw)
    assert tel["rssi"] == -70
    assert tel["tx_bytes_r"] == 3000
    assert tel["rx_bytes_r"] == 4000
    assert tel["name"] == "Office Phone"


def test_apply_telemetry_preserves_false_and_zero():
    device = type("D", (), {})()
    _apply_telemetry(
        device,
        {
            "is_wired": False,
            "channel": 0,
            "satisfaction": 0,
            "name": None,
        },
    )
    assert device.is_wired is False
    assert device.channel == 0
    assert device.satisfaction == 0
    assert not hasattr(device, "name")


def test_display_helpers():
    d = Device(
        mac="aa:bb:cc:dd:ee:ff",
        hostname="host",
        name="UniFi Name",
        display_name=None,
        vendor="OUI Vendor",
        dev_vendor="Apple",
        os_name="iOS",
        dev_family="smartphone",
        dev_cat="phone",
        is_wired=False,
        channel=6,
        rssi=-77,
        last_seen_at=datetime.utcnow(),
    )
    assert device_display_name(d) == "UniFi Name"
    d.display_name = "My Phone"
    assert device_display_name(d) == "My Phone"
    assert device_fingerprint_subtitle(d) == "Apple · iOS · smartphone"
    assert device_icon(d) == "smartphone"
    assert bytes_r_to_mbps(1_000_000) == 8.0
