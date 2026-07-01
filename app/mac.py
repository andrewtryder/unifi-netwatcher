import re

def normalize_mac(mac: str) -> str | None:
    """
    Normalizes a MAC address string to standard lowercase format with colons: xx:xx:xx:xx:xx:xx.
    Returns None if the MAC address is invalid.
    """
    if not mac:
        return None
    
    # Remove all non-hex characters
    cleaned = re.sub(r'[^0-9a-fA-F]', '', mac).lower()
    
    if len(cleaned) != 12:
        return None
        
    # Re-insert colons
    return ':'.join(cleaned[i:i+2] for i in range(0, 12, 2))
