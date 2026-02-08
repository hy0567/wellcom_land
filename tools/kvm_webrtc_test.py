"""KVM WebRTC signaling analysis - check ICE candidates"""
import socket
import sys, io, time, base64, os, struct, json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

ip = '10.147.17.133'
port = 18069

key = base64.b64encode(os.urandom(16)).decode()
req = (
    'GET /webrtc/signaling/client?id=test456 HTTP/1.1\r\n'
    f'Host: {ip}:{port}\r\n'
    'Upgrade: websocket\r\n'
    'Connection: Upgrade\r\n'
    f'Sec-WebSocket-Key: {key}\r\n'
    'Sec-WebSocket-Version: 13\r\n'
    f'Origin: http://{ip}:{port}\r\n'
    '\r\n'
)

def read_ws_frame(sock):
    """Read a WebSocket frame"""
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
    """Send a WebSocket text frame (client must mask)"""
    if isinstance(data, str):
        data = data.encode('utf-8')

    mask_key = os.urandom(4)
    masked_data = bytes(data[i] ^ mask_key[i % 4] for i in range(len(data)))

    frame = bytearray()
    frame.append(0x80 | opcode)  # FIN + opcode

    if len(data) < 126:
        frame.append(0x80 | len(data))  # MASK + length
    elif len(data) < 65536:
        frame.append(0x80 | 126)
        frame.extend(struct.pack('>H', len(data)))
    else:
        frame.append(0x80 | 127)
        frame.extend(struct.pack('>Q', len(data)))

    frame.extend(mask_key)
    frame.extend(masked_data)
    sock.sendall(frame)


print(f"Connecting to {ip}:{port}...")
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(15)
s.connect((ip, port))
s.sendall(req.encode())

# Read HTTP upgrade response
resp = b''
while b'\r\n\r\n' not in resp:
    resp += s.recv(4096)

headers = resp[:resp.index(b'\r\n\r\n')].decode()
print(f"Upgrade: {headers.split(chr(13))[0]}")

remaining = resp[resp.index(b'\r\n\r\n') + 4:]

# Parse remaining as WebSocket frame if present
if remaining:
    # Put it back by prepending
    pass

print("\n=== Reading WebSocket messages (20s) ===")
s.settimeout(3)
start = time.time()
msg_count = 0

while time.time() - start < 20:
    try:
        result = read_ws_frame(s)
        if result is None:
            print("Connection closed")
            break

        opcode, payload = result
        msg_count += 1

        try:
            text = payload.decode('utf-8')
            data = json.loads(text)
            print(f"\n[MSG {msg_count}] opcode={opcode}")
            print(json.dumps(data, indent=2, ensure_ascii=False)[:500])

            # Look for ICE candidates
            if 'candidate' in str(data).lower():
                print("  *** ICE CANDIDATE FOUND! ***")
            if 'sdp' in str(data).lower():
                sdp = str(data)
                # Find IP addresses in SDP
                import re
                ips = re.findall(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', sdp)
                if ips:
                    print(f"  *** IPs in SDP: {list(set(ips))} ***")
        except:
            print(f"[MSG {msg_count}] opcode={opcode}, binary {len(payload)} bytes")

    except socket.timeout:
        if time.time() - start > 15:
            break
        continue
    except Exception as e:
        print(f"Error: {e}")
        break

s.close()
print(f"\nDone. {msg_count} messages received.")
