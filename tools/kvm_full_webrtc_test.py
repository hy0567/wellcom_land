"""Full WebRTC signaling test - simulate browser behavior
Check if WebRTC media flows through the relay or tries direct connection"""
import socket
import sys, io, time, base64, os, struct, json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ip = '10.147.17.133'
port = 18069


def read_ws_frame(sock):
    header = b''
    while len(header) < 2:
        d = sock.recv(2 - len(header))
        if not d:
            return None
        header += d
    b1, b2 = header[0], header[1]
    opcode = b1 & 0x0F
    masked = (b2 & 0x80) != 0
    payload_len = b2 & 0x7F
    if payload_len == 126:
        ext = sock.recv(2)
        payload_len = struct.unpack('>H', ext)[0]
    elif payload_len == 127:
        ext = sock.recv(8)
        payload_len = struct.unpack('>Q', ext)[0]
    if masked:
        mask = sock.recv(4)
    payload = b''
    while len(payload) < payload_len:
        d = sock.recv(payload_len - len(payload))
        if not d:
            break
        payload += d
    if masked:
        payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
    return opcode, payload


def send_ws_frame(sock, data, opcode=1):
    if isinstance(data, str):
        data = data.encode('utf-8')
    mask_key = os.urandom(4)
    masked_data = bytes(data[i] ^ mask_key[i % 4] for i in range(len(data)))
    frame = bytearray()
    frame.append(0x80 | opcode)
    if len(data) < 126:
        frame.append(0x80 | len(data))
    elif len(data) < 65536:
        frame.append(0x80 | 126)
        frame.extend(struct.pack('>H', len(data)))
    else:
        frame.append(0x80 | 127)
        frame.extend(struct.pack('>Q', len(data)))
    frame.extend(mask_key)
    frame.extend(masked_data)
    sock.sendall(frame)


# Connect
key = base64.b64encode(os.urandom(16)).decode()
req = (
    f'GET /webrtc/signaling/client?id=fulltest789 HTTP/1.1\r\n'
    f'Host: {ip}:{port}\r\n'
    'Upgrade: websocket\r\n'
    'Connection: Upgrade\r\n'
    f'Sec-WebSocket-Key: {key}\r\n'
    'Sec-WebSocket-Version: 13\r\n'
    f'Origin: http://{ip}:{port}\r\n'
    '\r\n'
)

print(f"Connecting to {ip}:{port}...")
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(15)
s.connect((ip, port))
s.sendall(req.encode())

resp = b''
while b'\r\n\r\n' not in resp:
    resp += s.recv(4096)
print(f"WS connected")

# Read messages
print("\n=== Signaling messages ===")
s.settimeout(3)
start = time.time()

# Wait for device-metadata
while time.time() - start < 10:
    try:
        result = read_ws_frame(s)
        if result is None:
            break
        opcode, payload = result
        if opcode == 1:  # text
            data = json.loads(payload.decode())
            print(f"<- {data.get('type', '?')}: {json.dumps(data, ensure_ascii=False)[:200]}")
            if data.get('type') == 'device-metadata':
                break
        elif opcode == 9:  # ping
            send_ws_frame(s, payload, opcode=10)  # pong
            print(f"<- ping, -> pong")
    except socket.timeout:
        continue

# Now simulate sending a fake SDP offer to see what KVM returns
# Create a minimal SDP
fake_sdp = {
    "type": "offer",
    "sdp": (
        "v=0\r\n"
        "o=- 0 0 IN IP4 127.0.0.1\r\n"
        "s=-\r\n"
        "t=0 0\r\n"
        "a=group:BUNDLE 0\r\n"
        "a=msid-semantic: WMS\r\n"
        "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
        "c=IN IP4 0.0.0.0\r\n"
        "a=rtcp:9 IN IP4 0.0.0.0\r\n"
        "a=ice-ufrag:test\r\n"
        "a=ice-pwd:testpasswordfortesting123\r\n"
        "a=fingerprint:sha-256 00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00\r\n"
        "a=setup:actpass\r\n"
        "a=mid:0\r\n"
        "a=recvonly\r\n"
        "a=rtcp-mux\r\n"
        "a=rtpmap:96 VP8/90000\r\n"
    )
}

encoded_sdp = base64.b64encode(json.dumps(fake_sdp).encode()).decode()
offer_msg = json.dumps({
    "type": "offer",
    "data": {"sd": encoded_sdp}
})

print(f"\n-> Sending fake SDP offer...")
send_ws_frame(s, offer_msg)

# Wait for answer and ICE candidates
print("\n=== Waiting for answer/ICE candidates (15s) ===")
s.settimeout(3)
start = time.time()
import re

while time.time() - start < 15:
    try:
        result = read_ws_frame(s)
        if result is None:
            print("Connection closed")
            break
        opcode, payload = result
        if opcode == 1:
            text = payload.decode('utf-8')
            data = json.loads(text)
            msg_type = data.get('type', '?')
            print(f"\n<- type={msg_type}")

            if msg_type == 'answer':
                # Decode SDP answer
                try:
                    sdp_b64 = data.get('data', '')
                    sdp_json = json.loads(base64.b64decode(sdp_b64))
                    sdp_text = sdp_json.get('sdp', '')
                    print(f"  SDP answer:")
                    for line in sdp_text.split('\r\n'):
                        if line.startswith('a=candidate') or 'IP4' in line:
                            print(f"    {line}")
                    # Find IPs
                    ips = re.findall(r'\d+\.\d+\.\d+\.\d+', sdp_text)
                    print(f"  IPs in SDP: {list(set(ips))}")
                except:
                    print(f"  Raw: {str(data)[:300]}")

            elif msg_type == 'new-ice-candidate':
                candidate = data.get('data', {})
                cand_str = candidate.get('candidate', '')
                print(f"  ICE candidate: {cand_str}")
                ips = re.findall(r'\d+\.\d+\.\d+\.\d+', cand_str)
                if ips:
                    print(f"  *** IPs: {ips} ***")
            else:
                print(f"  {json.dumps(data, ensure_ascii=False)[:300]}")

        elif opcode == 9:
            send_ws_frame(s, payload, opcode=10)
    except socket.timeout:
        continue

s.close()
print("\nDone")
