from app.unifi.band import band_label, connection_mix_key


def test_band_label_wired():
    assert band_label(is_wired=True) == "Wired"


def test_band_label_24():
    assert band_label(is_wired=False, channel=6, radio_proto="ng") == "2.4 GHz"


def test_band_label_5():
    assert band_label(is_wired=False, channel=36, radio_proto="ax") == "5 GHz"
    assert band_label(is_wired=False, channel=149, radio_proto="ac") == "5 GHz"


def test_band_label_6():
    assert band_label(is_wired=False, channel=5, radio_proto="6e") == "6 GHz"
    assert band_label(is_wired=False, channel=200, radio_proto="ax") == "6 GHz"


def test_connection_mix_key():
    assert connection_mix_key(is_wired=True) == "wired"
    assert connection_mix_key(is_wired=False, channel=11) == "band_24"
    assert connection_mix_key(is_wired=False, channel=44) == "band_5"
