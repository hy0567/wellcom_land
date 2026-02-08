"""
KVM Relay — 관제 PC에서 로컬 KVM을 ZeroTier로 중계

관제 PC가 로컬 네트워크의 KVM을 발견하면:
1. 각 KVM에 대해 TCP 프록시 포트를 열어줌 (ZT_IP:18xxx → KVM_IP:80)
2. 서버에 등록하여 원격 PC(admin)가 접근 가능하게 함
3. 주기적으로 heartbeat 전송
"""

import socket
import threading
import time
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class TCPProxy:
    """단일 KVM에 대한 TCP 프록시 (ZT → 로컬 KVM)"""

    def __init__(self, listen_port: int, target_ip: str, target_port: int = 80):
        self.listen_port = listen_port
        self.target_ip = target_ip
        self.target_port = target_port
        self._server: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

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
            target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target_sock.settimeout(10)  # 연결 타임아웃
            target_sock.connect((self.target_ip, self.target_port))

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


class KVMRelayManager:
    """KVM 릴레이 관리자 — 발견된 KVM에 대해 프록시 자동 생성"""

    # 프록시 포트 시작 번호 (18000 + KVM IP의 마지막 옥텟)
    BASE_PORT = 18000

    def __init__(self):
        self._proxies: Dict[str, TCPProxy] = {}  # key: "kvm_ip:port"
        self._zt_ip: Optional[str] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._running = False

    def get_zt_ip(self) -> Optional[str]:
        """이 PC의 ZeroTier IP 가져오기"""
        if self._zt_ip:
            return self._zt_ip

        try:
            hostname = socket.gethostname()
            addr_infos = socket.getaddrinfo(hostname, None, socket.AF_INET)
            for info in addr_infos:
                ip = info[4][0]
                if ip.startswith('10.147.'):
                    self._zt_ip = ip
                    return ip
        except Exception:
            pass

        # UDP 연결 방식
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.1)
            s.connect(("10.147.17.1", 80))
            ip = s.getsockname()[0]
            s.close()
            if ip.startswith('10.147.'):
                self._zt_ip = ip
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

    def start_relay(self, kvm_ip: str, kvm_port: int = 80, kvm_name: str = "") -> Optional[int]:
        """KVM에 대한 TCP 프록시 시작

        Returns: 할당된 프록시 포트 (실패 시 None)
        """
        key = f"{kvm_ip}:{kvm_port}"
        if key in self._proxies:
            return self._proxies[key].listen_port

        relay_port = self.calc_relay_port(kvm_ip, kvm_port)

        # 포트 충돌 시 +1000 시도
        for offset in [0, 1000, 2000]:
            port = relay_port + offset
            proxy = TCPProxy(port, kvm_ip, kvm_port)
            proxy.start()
            if proxy._running:
                self._proxies[key] = proxy
                logger.info(f"[Relay] {kvm_name or kvm_ip} → :{port}")
                return port
            proxy.stop()

        logger.error(f"[Relay] {kvm_ip} 프록시 포트 할당 실패")
        return None

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

    def get_relay_info(self) -> List[dict]:
        """현재 활성 릴레이 정보"""
        zt_ip = self.get_zt_ip()
        result = []
        for key, proxy in self._proxies.items():
            kvm_ip, kvm_port = key.rsplit(':', 1)
            result.append({
                "kvm_local_ip": kvm_ip,
                "kvm_port": int(kvm_port),
                "relay_port": proxy.listen_port,
                "relay_zt_ip": zt_ip or "",
                "access_url": f"http://{zt_ip}:{proxy.listen_port}" if zt_ip else "",
            })
        return result

    def register_to_server(self, api_client, location: str = ""):
        """서버에 현재 릴레이 중인 KVM들을 등록"""
        zt_ip = self.get_zt_ip()
        if not zt_ip:
            logger.warning("[Relay] ZeroTier IP 없음 — 서버 등록 건너뜀")
            return

        devices = []
        for key, proxy in self._proxies.items():
            kvm_ip, kvm_port = key.rsplit(':', 1)
            devices.append({
                "kvm_local_ip": kvm_ip,
                "kvm_port": int(kvm_port),
                "kvm_name": f"KVM-{kvm_ip.split('.')[-1]}",
                "relay_port": proxy.listen_port,
            })

        if not devices:
            return

        try:
            result = api_client._post('/api/kvm/register', {
                "devices": devices,
                "relay_zt_ip": zt_ip,
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
                zt_ip = self.get_zt_ip()
                if zt_ip:
                    try:
                        api_client._post('/api/kvm/heartbeat', {
                            "relay_zt_ip": zt_ip,
                        })
                    except Exception:
                        pass
                time.sleep(interval)

        self._heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()
