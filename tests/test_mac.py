from app.mac import normalize_mac

def test_normalize_mac():
    assert normalize_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aabbccddeeff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    
def test_invalid_mac():
    assert normalize_mac("invalid") is None
    assert normalize_mac("") is None
    assert normalize_mac(None) is None
    assert normalize_mac("aa:bb:cc:dd:ee") is None # too short
