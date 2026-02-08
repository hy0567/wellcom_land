"""Deep analysis of KVM JS - find signaling protocol"""
import urllib.request
import re
import sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

url = 'http://10.147.17.133:18069/static/assets/index-B4UlkwE2.js'
print("Downloading JS...")
resp = urllib.request.urlopen(url, timeout=30)
js = resp.read().decode('utf-8', errors='replace')

# Find signaling related code
print("=== WebRTC Signaling Flow ===")

# Find context around RTCPeerConnection creation
idx = js.find('RTCPeerConnection')
if idx > 0:
    ctx = js[max(0,idx-500):idx+1000]
    print(f"[RTCPeerConnection context]")
    print(ctx[:1500])
    print()

# Find context around createOffer
idx = js.find('createOffer')
if idx > 0:
    ctx = js[max(0,idx-300):idx+500]
    print(f"[createOffer context]")
    print(ctx[:800])
    print()

# Find context around setRemoteDescription
idx = js.find('setRemoteDescription')
if idx > 0:
    ctx = js[max(0,idx-300):idx+500]
    print(f"[setRemoteDescription context]")
    print(ctx[:800])
    print()

# Find sendMessage patterns
print("=== sendMessage / signal patterns ===")
for pattern in [r'sendMessage\([^)]{1,200}\)', r'send\(JSON\.stringify']:
    for m in re.finditer(pattern, js):
        ctx = js[max(0,m.start()-100):m.end()+100]
        print(f"  {ctx[:300]}")
        print()

# Find ICE candidate handling
print("=== ICE Candidate ===")
idx = js.find('onicecandidate')
if idx > 0:
    ctx = js[max(0,idx-200):idx+500]
    print(ctx[:700])
    print()

# Find host/IP related patterns in signaling
print("=== Host/IP in signaling ===")
for pattern in [r'iceServers', r'stun:', r'turn:']:
    for m in re.finditer(pattern, js, re.IGNORECASE):
        ctx = js[max(0,m.start()-100):m.end()+200]
        print(f"[{pattern}] {ctx[:400]}")
        print()
