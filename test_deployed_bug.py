#!/usr/bin/env python3
import hashlib

def deployed_generate_compliant_event_id(uid: str) -> str:
    """The currently deployed buggy version."""
    # Create hash from UID
    hash_bytes = hashlib.sha256(uid.encode()).digest()
    
    # Use restricted alphabet allowed by Google: lowercase letters, digits, dash, underscore
    # We'll base32hex-encode then remap to allowed set if needed
    base32hex_alphabet = '0123456789abcdefghijklmnopqrstuv'
    
    # Manual base32hex encoding to ensure compliance
    def base32hex_encode(data: bytes) -> str:
        """Encode bytes using base32hex alphabet (RFC2938)."""
        bits = ''.join(format(byte, '08b') for byte in data)
        # Pad to multiple of 5 bits
        while len(bits) % 5 != 0:
            bits += '0'
        
        result = ''
        for i in range(0, len(bits), 5):
            chunk = bits[i:i+5]
            result += base32hex_alphabet[int(chunk, 2)]
        
        return result
    
    # Generate the base32hex encoded ID, then adapt to Google's allowed charset
    base = base32hex_encode(hash_bytes)
    # Truncate for practicality
    if len(base) > 32:
        base = base[:32]
    # Remap to allowed set (a-z0-9_-) by translating any 'w'..'v' unused to letters/digits, though
    # base32hex already yields 0-9 and a-v only; all are lowercase letters/digits, which are allowed.
    event_id = base
    # Ensure minimum length
    if len(event_id) < 5:
        event_id = event_id + '0' * (5 - len(event_id))
    # Ensure starts with a letter to avoid opaque backend constraints
    if not ('a' <= event_id[0] <= 'z'):
        event_id = 'e' + event_id[:-1]
    # Replace any disallowed chars with '-' (should not occur from base32hex)
    allowed = set('abcdefghijklmnopqrstuvwxyz0123456789-_')  # BUG: allows w,x,y,z,_,-
    event_id = ''.join(c if c in allowed else '-' for c in event_id)
    return event_id

# Test with failing UIDs
uids = ['869CF047-C3A9-4199-9C0C-0772A375A5FB', '3E262B76-4B59-44EC-90B2-E2FF626E20D0']

for uid in uids:
    event_id = deployed_generate_compliant_event_id(uid)
    print(f'UID: {uid}')
    print(f'Generated ID: {event_id}')
    print(f'Length: {len(event_id)}')
    
    # Check for invalid characters
    valid_base32hex = set('0123456789abcdefghijklmnopqrstuv')
    invalid_chars = set(event_id) - valid_base32hex
    print(f'Invalid chars: {invalid_chars}')
    print(f'Valid base32hex?: {len(invalid_chars) == 0}')
    print('---')