"""
KVM Relay Diagnostics
"""
import socket
import sys
import time
import io

# Windows cp949 encoding fix
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 관제 PC ZeroTier IP
RELAY_IP = "10.147.17.133"

# 릴레이 포트 목록
PORTS = [18069, 18070, 18061]


def test_tcp_connect(ip, port, timeout=5):
    """TCP 연결 테스트"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        start = time.time()
        s.connect((ip, port))
        elapsed = (time.time() - start) * 1000
        s.close()
        return True, f"{elapsed:.0f}ms"
    except socket.timeout:
        return False, "timeout"
    except ConnectionRefusedError:
        return False, "refused"
    except OSError as e:
        return False, str(e)


def test_http_get(ip, port, timeout=10):
    """HTTP GET 테스트 (raw socket)"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((ip, port))

        # HTTP 요청 전송
        req = f"GET / HTTP/1.1\r\nHost: {ip}:{port}\r\nConnection: close\r\n\r\n"
        s.sendall(req.encode())

        # 응답 읽기
        response = b""
        while True:
            try:
                data = s.recv(4096)
                if not data:
                    break
                response += data
                if len(response) > 2048:
                    break
            except socket.timeout:
                break

        s.close()

        if response:
            # 첫 줄만 파싱
            first_line = response.split(b'\r\n')[0].decode('utf-8', errors='replace')
            content_len = len(response)
            return True, f"{first_line} ({content_len} bytes)"
        else:
            return False, "no response"

    except Exception as e:
        return False, str(e)


def test_websocket_upgrade(ip, port, timeout=10):
    """WebSocket 업그레이드 테스트 (KVM에서 사용)"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((ip, port))

        # WebSocket 업그레이드 요청
        import base64, os
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET /websockify HTTP/1.1\r\n"
            f"Host: {ip}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        s.sendall(req.encode())

        # 응답 읽기
        response = b""
        while True:
            try:
                data = s.recv(4096)
                if not data:
                    break
                response += data
                if b'\r\n\r\n' in response:
                    break
            except socket.timeout:
                break

        s.close()

        if response:
            first_line = response.split(b'\r\n')[0].decode('utf-8', errors='replace')
            return True, first_line
        else:
            return False, "no response"

    except Exception as e:
        return False, str(e)


def main():
    print(f"=" * 60)
    print(f"KVM 릴레이 진단 — 대상: {RELAY_IP}")
    print(f"=" * 60)

    # 0. 이 PC의 IP 확인
    print(f"\n[0] 이 PC의 네트워크 인터페이스:")
    try:
        hostname = socket.gethostname()
        addrs = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for info in addrs:
            ip = info[4][0]
            tag = ""
            if ip.startswith('10.147.'):
                tag = " (ZeroTier)"
            elif ip.startswith('192.168.'):
                tag = " (LAN)"
            elif ip.startswith('169.254.'):
                tag = " (APIPA)"
            print(f"  - {ip}{tag}")
    except Exception as e:
        print(f"  오류: {e}")

    # 1. Ping (ICMP는 제한될 수 있으므로 TCP로 대체)
    print(f"\n[1] TCP 연결 테스트:")
    for port in PORTS:
        ok, msg = test_tcp_connect(RELAY_IP, port)
        status = "✅ OPEN" if ok else "❌ CLOSED"
        print(f"  {RELAY_IP}:{port} → {status} ({msg})")

    # 2. HTTP GET 테스트
    print(f"\n[2] HTTP GET 테스트:")
    for port in PORTS:
        ok, msg = test_http_get(RELAY_IP, port)
        status = "✅" if ok else "❌"
        print(f"  {RELAY_IP}:{port} → {status} {msg}")

    # 3. WebSocket 업그레이드 테스트
    print(f"\n[3] WebSocket 업그레이드 테스트:")
    for port in PORTS:
        ok, msg = test_websocket_upgrade(RELAY_IP, port)
        status = "✅" if ok else "❌"
        print(f"  {RELAY_IP}:{port} → {status} {msg}")

    # 4. 장기 연결 테스트 (MJPEG 스트림 시뮬레이션)
    print(f"\n[4] 장기 연결 테스트 (15초):")
    for port in PORTS[:1]:  # 첫 번째 포트만 테스트
        print(f"  {RELAY_IP}:{port} — MJPEG 스트림 테스트...")
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect((RELAY_IP, port))

            req = f"GET /stream HTTP/1.1\r\nHost: {ip}:{port}\r\nConnection: keep-alive\r\n\r\n"
            s.sendall(req.encode())

            total = 0
            start = time.time()
            s.settimeout(3)

            while time.time() - start < 15:
                try:
                    data = s.recv(65536)
                    if not data:
                        print(f"  → 서버가 연결을 닫음 ({time.time() - start:.1f}초, {total} bytes)")
                        break
                    total += len(data)
                except socket.timeout:
                    elapsed = time.time() - start
                    print(f"  → {elapsed:.1f}초 경과, {total} bytes 수신 (대기 중...)")
                    if elapsed > 10:
                        break

            s.close()
            elapsed = time.time() - start
            print(f"  → 완료: {elapsed:.1f}초, 총 {total} bytes")

        except Exception as e:
            print(f"  → 오류: {e}")

    print(f"\n{'=' * 60}")
    print("진단 완료")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()
