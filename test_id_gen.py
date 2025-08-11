#!/usr/bin/env python3
import hashlib

uid = '869CF047-C3A9-4199-9C0C-0772A375A5FB'
hash_bytes = hashlib.sha256(uid.encode()).digest()
print('UID:', uid)
print('Hash bytes:', hash_bytes.hex())

# Manual base32hex encoding
base32hex_alphabet = '0123456789abcdefghijklmnopqrstuv'
bits = ''.join(format(byte, '08b') for byte in hash_bytes)
while len(bits) % 5 != 0:
    bits += '0'

result = ''
for i in range(0, len(bits), 5):
    chunk = bits[i:i+5]
    result += base32hex_alphabet[int(chunk, 2)]

# Truncate and ensure starts with letter
event_id = result[:32]
if event_id[0].isdigit():
    event_id = chr(ord('a') + int(event_id[0])) + event_id[1:]

print('Generated ID:', event_id)
print('Length:', len(event_id))
print('Characters:', set(event_id))
print('Valid base32hex?', set(event_id).issubset(set('0123456789abcdefghijklmnopqrstuv')))