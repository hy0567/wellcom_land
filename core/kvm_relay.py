"""
KVM Relay — 관제 PC에서 로컬 KVM을 Tailscale로 중계

관제 PC가 로컬 네트워크의 KVM을 발견하면:
1. 각 KVM에 대해 TCP 프록시 포트를 열어줌 (Tailscale_IP:18xxx → KVM_IP:80)
2. 각 KVM에 대해 UDP 릴레이 포트를 열어줌 (WebRTC 미디어용)
3. 서버에 등록하여 원격 PC(admin)가 접근 가능하게 함
4. 주기적으로 heartbeat 전송
"""

import socket
import threading
import time
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TCPProxy:
    """단일 KVM에 대한 TCP 프록시 (Tailscale → 로컬 KVM)"""

    def __init__(self, listen_port: int, target_ip: str, target_port: int = 80,
                 on_udp_port_detected: Optional[callable] = None):
        self.listen_port = listen_port
        self.target_ip = target_ip
        self.target_port = target_port
        self._server: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # 콜백: ICE candidate에서 UDP 포트 추출 시 호출
        self._on_udp_port_detected = on_udp_port_detected

    def start(self):
        """프록시 서버 시작"""
        if self._running:
            return

        try:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server.bind(('0.0.0.0', self.listen_port))
            self._server.listen(5)
            self._server.settimeout(1.0)
            self._running = True

            self._thread = threading.Thread(target=self._accept_loop, daemon=True)
            self._thread.start()
            logger.info(f"[Relay] :{self.listen_port} → {self.target_ip}:{self.target_port}")
        except Exception as e:
            logger.error(f"[Relay] 포트 {self.listen_port} 바인드 실패: {e}")
            self._running = False

    def stop(self):
        """프록시 서버 중지"""
        self._running = False
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None

    def _accept_loop(self):
        """연결 수락 루프"""
        while self._running:
            try:
                client_sock, addr = self._server.accept()
                # 각 연결을 별도 스레드에서 처리
                t = threading.Thread(
                    target=self._relay,
                    args=(client_sock,),
                    daemon=True
                )
                t.start()
            except socket.timeout:
                continue
            except Exception:
                if self._running:
                    continue
                break

    def _relay(self, client_sock: socket.socket):
        """양방향 데이터 릴레이"""
        target_sock = None
        try:
            # 첫 번째 데이터를 먼저 읽어서 특수 경로 확인
            client_sock.settimeout(10)
            first_data = client_sock.recv(4096)
            if not first_data:
                return

            # /_wellcomland/ 특수 경로 처리 (UDP 포트 알림 등)
            if self._handle_special_request(client_sock, first_data):
                return

            target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target_sock.settimeout(10)  # 연결 타임아웃
            target_sock.connect((self.target_ip, self.target_port))

            # 첫 번째 데이터를 KVM에 전달
            target_sock.sendall(first_data)

            # 연결 성공 후 blocking 모드로 전환 (장기 연결 지원: MJPEG/WebSocket)
            target_sock.settimeout(None)
            client_sock.settimeout(None)

            # TCP_NODELAY — 입력 지연 최소화 (키보드/마우스 이벤트)
            for s in (client_sock, target_sock):
                try:
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except Exception:
                    pass

            # 양방향 릴레이
            t1 = threading.Thread(
                target=self._pipe, args=(client_sock, target_sock), daemon=True
            )
            t2 = threading.Thread(
                target=self._pipe, args=(target_sock, client_sock), daemon=True
            )
            t1.start()
            t2.start()
            t1.join()
            t2.join()
        except Exception as e:
            logger.debug(f"[Relay] 릴레이 연결 실패 ({self.target_ip}:{self.target_port}): {e}")
        finally:
            try:
                client_sock.close()
            except Exception:
                pass
            if target_sock:
                try:
                    target_sock.close()
                except Exception:
                    pass

    def _handle_special_request(self, client_sock: socket.socket, data: bytes) -> bool:
        """/_wellcomland/ 특수 경로 처리

        ICE 패치 JS에서 KVM의 WebRTC UDP 포트를 알려주는 요청 처리.
        Returns: True면 특수 요청으로 처리 완료 (KVM에 전달하지 않음)
        """
        try:
            text = data.decode('utf-8', errors='ignore')

            # HTTP GET /_wellcomland/set_udp_port?port=55234
            if '/_wellcomland/set_udp_port' in text:
                import re
                m = re.search(r'port=(\d+)', text)
                if m and self._on_udp_port_detected:
                    udp_port = int(m.group(1))
                    logger.info(f"[Relay] ICE candidate에서 KVM UDP 포트 수신: {self.target_ip}:{udp_port}")
                    self._on_udp_port_detected(self.target_ip, udp_port)

                # HTTP 응답
                resp = (
                    "HTTP/1.1 200 OK\r\n"
                    "Access-Control-Allow-Origin: *\r\n"
                    "Content-Type: application/json\r\n"
                    "Content-Length: 15\r\n"
                    "Connection: close\r\n\r\n"
                    '{"status":"ok"}'
                )
                client_sock.sendall(resp.encode())
                return True

            # OPTIONS (CORS preflight)
            if text.startswith('OPTIONS') and '/_wellcomland/' in text:
                resp = (
                    "HTTP/1.1 204 No Content\r\n"
                    "Access-Control-Allow-Origin: *\r\n"
                    "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
                    "Access-Control-Allow-Headers: *\r\n"
                    "Connection: close\r\n\r\n"
                )
                client_sock.sendall(resp.encode())
                return True

        except Exception as e:
            logger.debug(f"[Relay] 특수 요청 처리 오류: {e}")

        return False

    @staticmethod
    def _pipe(src: socket.socket, dst: socket.socket):
        """한 방향 데이터 전달 (MJPEG/WebSocket 장기 스트림 지원)"""
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass  # 정상적인 연결 종료
        except OSError:
            pass  # 소켓 이미 닫힘
        except Exception:
            pass
        finally:
            # 안전하게 종료 — 상대방에게 EOF 알림
            try:
                dst.shutdown(socket.SHUT_WR)
            except Exception:
                pass


class UDPRelay:
    """WebRTC 미디어용 UDP 릴레이 (Tailscale → 로컬 KVM)

    WebRTC는 DTLS/SRTP를 UDP로 전송.
    원격 클라이언트가 relay_ip:udp_port 로 보내면 → kvm_ip:kvm_udp_port 로 전달.
    KVM이 응답하면 → 원격 클라이언트에게 그대로 전달.
    """

    def __init__(self, listen_port: int, target_ip: str):
        self.listen_port = listen_port
        self.target_ip = target_ip
        # KVM의 실제 UDP 포트는 ICE candidate에서 동적으로 결정됨
        self._target_port: Optional[int] = None
        self._sock: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # 원격 클라이언트 주소 (첫 패킷에서 학습)
        self._remote_addr: Optional[Tuple[str, int]] = None

    def start(self) -> bool:
        """UDP 릴레이 시작"""
        if self._running:
            return True
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(('0.0.0.0', self.listen_port))
            self._sock.settimeout(1.0)
            self._running = True
            self._thread = threading.Thread(target=self._relay_loop, daemon=True)
            self._thread.start()
            logger.info(f"[UDPRelay] :{self.listen_port} → {self.target_ip} (UDP)")
            return True
        except Exception as e:
            logger.error(f"[UDPRelay] 포트 {self.listen_port} 바인드 실패: {e}")
            self._running = False
            return False

    def set_target_port(self, port: int):
        """KVM의 실제 WebRTC UDP 포트 설정 (ICE candidate에서 추출)"""
        if self._target_port != port:
            self._target_port = port
            logger.info(f"[UDPRelay] 타겟 포트 설정: {self.target_ip}:{port}")

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _relay_loop(self):
        """양방향 UDP 릴레이 루프"""
        while self._running:
            try:
                data, addr = self._sock.recvfrom(65536)
                if not data:
                    continue

                # KVM에서 온 패킷인지 원격 클라이언트에서 온 패킷인지 판별
                if addr[0] == self.target_ip:
                    # KVM → 원격 클라이언트
                    if self._remote_addr:
                        self._sock.sendto(data, self._remote_addr)
                else:
                    # 원격 클라이언트 → KVM
                    self._remote_addr = addr
                    if self._target_port:
                        self._sock.sendto(data, (self.target_ip, self._target_port))

            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    continue
                break
            except Exception:
                if self._running:
                    continue
                break


class KVMRelayManager:
    """KVM 릴레이 관리자 — 발견된 KVM에 대해 프록시 자동 생성"""

    # 프록시 포트 시작 번호 (18000 + KVM IP의 마지막 옥텟)
    BASE_PORT = 18000
    # UDP 릴레이 포트 시작 번호 (28000 + KVM IP의 마지막 옥텟)
    UDP_BASE_PORT = 28000

    def __init__(self):
        self._proxies: Dict[str, TCPProxy] = {}  # key: "kvm_ip:port"
        self._udp_relays: Dict[str, UDPRelay] = {}  # key: "kvm_ip"
        self._tailscale_ip: Optional[str] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._running = False

    def get_tailscale_ip(self) -> Optional[str]:
        """이 PC의 Tailscale IP 가져오기 (100.x.x.x)"""
        if self._tailscale_ip:
            return self._tailscale_ip

        # 방법 1: tailscale CLI
        try:
            import subprocess
            r = subprocess.run(
                ['tailscale', 'ip', '-4'],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000
            )
            if r.returncode == 0:
                ip = r.stdout.strip().split('\n')[0].strip()
                if ip.startswith('100.'):
                    self._tailscale_ip = ip
                    return ip
        except Exception:
            pass

        # 방법 2: hostname resolver에서 100.x 찾기
        try:
            hostname = socket.gethostname()
            addr_infos = socket.getaddrinfo(hostname, None, socket.AF_INET)
            for info in addr_infos:
                ip = info[4][0]
                if ip.startswith('100.'):
                    self._tailscale_ip = ip
                    return ip
        except Exception:
            pass

        # 방법 3: UDP 연결 방식 (Tailscale Magic DNS)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.1)
            s.connect(("100.100.100.100", 80))
            ip = s.getsockname()[0]
            s.close()
            if ip.startswith('100.'):
                self._tailscale_ip = ip
                return ip
        except Exception:
            pass

        return None

    def calc_relay_port(self, kvm_ip: str, kvm_port: int = 80) -> int:
        """KVM IP로부터 프록시 포트 계산

        192.168.68.100:80  → 18100
        192.168.68.101:80  → 18101
        192.168.1.50:80    → 18050
        포트가 80이 아니면:  → 19000 + last_octet
        """
        try:
            last_octet = int(kvm_ip.split('.')[-1])
            if kvm_port == 80:
                return self.BASE_PORT + last_octet
            else:
                return 19000 + last_octet
        except (ValueError, IndexError):
            return self.BASE_PORT

    def calc_udp_port(self, kvm_ip: str) -> int:
        """KVM IP로부터 UDP 릴레이 포트 계산"""
        try:
            last_octet = int(kvm_ip.split('.')[-1])
            return self.UDP_BASE_PORT + last_octet
        except (ValueError, IndexError):
            return self.UDP_BASE_PORT

    def start_relay(self, kvm_ip: str, kvm_port: int = 80, kvm_name: str = "") -> Optional[int]:
        """KVM에 대한 TCP + UDP 프록시 시작

        Returns: 할당된 TCP 프록시 포트 (실패 시 None)
        """
        key = f"{kvm_ip}:{kvm_port}"
        if key in self._proxies:
            return self._proxies[key].listen_port

        relay_port = self.calc_relay_port(kvm_ip, kvm_port)

        # TCP 프록시 — 포트 충돌 시 +1000 시도
        for offset in [0, 1000, 2000]:
            port = relay_port + offset
            proxy = TCPProxy(port, kvm_ip, kvm_port,
                             on_udp_port_detected=self.set_udp_target_port)
            proxy.start()
            if proxy._running:
                self._proxies[key] = proxy
                logger.info(f"[Relay] {kvm_name or kvm_ip} TCP :{port}")
                break
            proxy.stop()
        else:
            logger.error(f"[Relay] {kvm_ip} TCP 프록시 포트 할당 실패")
            return None

        # UDP 릴레이 (WebRTC 미디어용)
        if kvm_ip not in self._udp_relays:
            udp_port = self.calc_udp_port(kvm_ip)
            for offset in [0, 1000, 2000]:
                udp = UDPRelay(udp_port + offset, kvm_ip)
                if udp.start():
                    self._udp_relays[kvm_ip] = udp
                    logger.info(f"[Relay] {kvm_name or kvm_ip} UDP :{udp_port + offset}")
                    break
                udp.stop()

        return self._proxies[key].listen_port

    def stop_relay(self, kvm_ip: str, kvm_port: int = 80):
        """특정 KVM 프록시 중지"""
        key = f"{kvm_ip}:{kvm_port}"
        if key in self._proxies:
            self._proxies[key].stop()
            del self._proxies[key]

    def stop_all(self):
        """모든 프록시 중지"""
        self._running = False
        for proxy in self._proxies.values():
            proxy.stop()
        self._proxies.clear()
        for udp in self._udp_relays.values():
            udp.stop()
        self._udp_relays.clear()

    def get_udp_port(self, kvm_ip: str) -> Optional[int]:
        """특정 KVM에 대한 UDP 릴레이 포트 조회"""
        udp = self._udp_relays.get(kvm_ip)
        return udp.listen_port if udp else None

    def set_udp_target_port(self, kvm_ip: str, kvm_udp_port: int):
        """KVM의 WebRTC UDP 포트 설정 (ICE candidate에서 추출한 포트)"""
        udp = self._udp_relays.get(kvm_ip)
        if udp:
            udp.set_target_port(kvm_udp_port)

    def get_relay_info(self) -> List[dict]:
        """현재 활성 릴레이 정보"""
        ts_ip = self.get_tailscale_ip()
        result = []
        for key, proxy in self._proxies.items():
            kvm_ip, kvm_port = key.rsplit(':', 1)
            udp_port = self.get_udp_port(kvm_ip)
            result.append({
                "kvm_local_ip": kvm_ip,
                "kvm_port": int(kvm_port),
                "relay_port": proxy.listen_port,
                "udp_relay_port": udp_port,
                "relay_ip": ts_ip or "",
                "access_url": f"http://{ts_ip}:{proxy.listen_port}" if ts_ip else "",
            })
        return result

    def register_to_server(self, api_client, location: str = ""):
        """서버에 현재 릴레이 중인 KVM들을 등록"""
        ts_ip = self.get_tailscale_ip()
        if not ts_ip:
            logger.warning("[Relay] Tailscale IP 없음 — 서버 등록 건너뜀")
            return

        devices = []
        for key, proxy in self._proxies.items():
            kvm_ip, kvm_port = key.rsplit(':', 1)
            udp_port = self.get_udp_port(kvm_ip)
            devices.append({
                "kvm_local_ip": kvm_ip,
                "kvm_port": int(kvm_port),
                "kvm_name": f"KVM-{kvm_ip.split('.')[-1]}",
                "relay_port": proxy.listen_port,
                "udp_relay_port": udp_port,
            })

        if not devices:
            return

        try:
            result = api_client._post('/api/kvm/register', {
                "devices": devices,
                "relay_ip": ts_ip,
                "location": location,
            })
            logger.info(f"[Relay] 서버 등록: {result}")
        except Exception as e:
            logger.error(f"[Relay] 서버 등록 실패: {e}")

    def start_heartbeat(self, api_client, interval: int = 60):
        """주기적 heartbeat 전송 시작"""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return

        self._running = True

        def _heartbeat_loop():
            while self._running:
                ts_ip = self.get_tailscale_ip()
                if ts_ip:
                    try:
                        api_client._post('/api/kvm/heartbeat', {
                            "relay_ip": ts_ip,
                        })
                    except Exception:
                        pass
                time.sleep(interval)

        self._heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()
