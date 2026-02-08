"""Check KVM ICE server config and find where iceServers come from"""
import urllib.request
import re
import sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

url = 'http://10.147.17.133:18069/static/assets/index-B4UlkwE2.js'
print("Downloading JS...")
resp = urllib.request.urlopen(url, timeout=30)
js = resp.read().decode('utf-8', errors='replace')

# Find i5e variable (used in iceServers condition)
print("=== i5e context ===")
for m in re.finditer(r'i5e', js):
    ctx = js[max(0, m.start()-200):m.end()+200]
    print(ctx[:500])
    print("---")

# Find where iceServers config is set
print("\n=== iceServers source ===")
for m in re.finditer(r'iceServers\s*[=:]', js):
    ctx = js[max(0, m.start()-200):m.end()+300]
    print(ctx[:600])
    print("---")

# Find device-metadata handling
print("\n=== device-metadata ===")
idx = js.find('device-metadata')
if idx > 0:
    ctx = js[max(0, idx-300):idx+500]
    print(ctx[:800])
    print("---")

# Find answer/offer handling
print("\n=== answer handling ===")
idx = js.find('"answer"')
if idx > 0:
    ctx = js[max(0, idx-300):idx+500]
    print(ctx[:800])

# Find TURN server references
print("\n=== TURN/STUN patterns ===")
for pattern in ['stun:', 'turn:', 'TURN', 'STUN']:
    for m in re.finditer(pattern, js):
        ctx = js[max(0, m.start()-100):m.end()+200]
        # Filter out false positives
        if 'Generator' not in ctx and 'return' not in ctx[:20]:
            print(f"[{pattern}] {ctx[:400]}")
            print("---")
