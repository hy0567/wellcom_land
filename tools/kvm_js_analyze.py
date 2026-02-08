"""KVM JS analysis - find WebSocket/WebRTC patterns"""
import urllib.request
import re
import sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

url = 'http://10.147.17.133:18069/static/assets/index-B4UlkwE2.js'
print(f"Downloading {url}...")
resp = urllib.request.urlopen(url, timeout=30)
js = resp.read().decode('utf-8', errors='replace')
print(f"JS size: {len(js)} chars")
print()

patterns = [
    (r'new\s+WebSocket\s*\([^)]{1,200}\)', 'WebSocket creation'),
    (r'wss?://[^\s"\'`]{1,100}', 'WebSocket URL'),
    (r'RTCPeerConnection', 'WebRTC PeerConnection'),
    (r'janus', 'Janus'),
    (r'/stream', 'Stream endpoint'),
    (r'/mjpeg', 'MJPEG'),
    (r'/video', 'Video endpoint'),
    (r'location\.host', 'location.host'),
    (r'location\.hostname', 'location.hostname'),
    (r'createOffer', 'WebRTC offer'),
    (r'setRemoteDescription', 'WebRTC SDP'),
    (r'/whep', 'WHEP'),
    (r'/webrtc', 'WebRTC endpoint'),
    (r'websocket', 'websocket ref'),
    (r'/api/ws', 'WS API'),
    (r'\.onmessage', 'onmessage handler'),
    (r'ICE', 'ICE'),
]

for pattern, label in patterns:
    matches = re.findall(pattern, js, re.IGNORECASE)
    if matches:
        unique = list(set(matches))[:8]
        print(f"[{label}] {len(matches)} matches:")
        for m in unique:
            idx = js.find(m)
            start = max(0, idx - 80)
            end = min(len(js), idx + len(m) + 80)
            ctx = js[start:end].replace('\n', ' ').strip()
            print(f"  {ctx}")
        print()
