"""
KVM 장치 자동 검색 모듈
동일 내부망에서 Luckfox PicoKVM 장치를 자동으로 탐지
"""

import socket
import threading
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional, Callable
from dataclasses import dataclass
import requests
from PyQt6.QtCore import QObject, pyqtSignal, QThread


@dataclass
class DiscoveredDevice:
    """발견된 장치 정보"""
    ip: str
    port: int = 80
    name: str = ""
    model: str = ""
    version: str = ""
    mac: str = ""
    is_online: bool = True


class NetworkScanner:
    """네트워크 스캐너 - 동기 방식"""

    # PicoKVM 기본 포트들
    DEFAULT_PORTS = [80, 8080, 443]

    # 요청 타임아웃 (초)
    TIMEOUT = 1.5

    @staticmethod
    def _get_all_ipv4_addresses() -> List[str]:
        """모든 IPv4 주소 수집 (순수 Python - PowerShell/cmd 불필요)

        방법 1: socket.getaddrinfo (크로스 플랫폼)
        방법 2: ctypes Windows API (경량 Windows 대응)
        방법 3: PowerShell 폴백 (가능한 경우)
        """
        all_ips = []

        # 방법 1: socket.getaddrinfo + hostname
        try:
            hostname = socket.gethostname()
            addr_infos = socket.getaddrinfo(hostname, None, socket.AF_INET)
            for info in addr_infos:
                ip = info[4][0]
                if ip != '127.0.0.1' and ip not in all_ips:
                    all_ips.append(ip)
        except Exception:
            pass

        # 방법 2: 여러 대상에 UDP 연결하여 각 인터페이스 IP 수집
        test_targets = [
            ("192.168.0.1", 80),    # 일반 LAN 게이트웨이
            ("192.168.1.1", 80),    # 일반 LAN 게이트웨이
            ("192.168.68.1", 80),   # 일부 LAN 게이트웨이
            ("10.0.0.1", 80),       # 10.x 대역
            ("172.16.0.1", 80),     # 172 사설 대역
            ("8.8.8.8", 80),        # 인터넷 (기본 라우트)
        ]
        for target_ip, target_port in test_targets:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.1)
                s.connect((target_ip, target_port))
                ip = s.getsockname()[0]
                s.close()
                if ip != '127.0.0.1' and ip not in all_ips:
                    all_ips.append(ip)
            except Exception:
                pass

        # 방법 3: PowerShell 폴백 (가능한 경우에만)
        if not all_ips:
            try:
                import subprocess
                result = subprocess.run(
                    ['powershell', '-Command',
                     "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' } | Select-Object -ExpandProperty IPAddress"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=0x08000000  # CREATE_NO_WINDOW
                )
                for ip in result.stdout.strip().split('\n'):
                    ip = ip.strip()
                    if ip and ip != '127.0.0.1' and ip not in all_ips:
                        all_ips.append(ip)
            except Exception:
                pass

        return all_ips

    @staticmethod
    def _classify_ip(ip: str) -> int:
        """IP 주소 우선순위 분류 (낮을수록 우선)

        Returns:
            0: 192.168.x.x, 10.x.x.x (일반 LAN) - 최우선
            1: 172.16-31.x.x (사설 네트워크)
            2: 기타 사설
            3: 100.x (Tailscale/CGNAT)
            4: 169.254.x (APIPA/링크로컬)
        """
        if ip.startswith('192.168.') or ip.startswith('10.'):
            return 0
        elif ip.startswith('172.'):
            parts = ip.split('.')
            try:
                if 16 <= int(parts[1]) <= 31:
                    return 1
            except (IndexError, ValueError):
                pass
            return 2
        elif ip.startswith('100.'):
            return 3  # Tailscale / CGNAT
        elif ip.startswith('169.254.'):
            return 4  # APIPA
        return 2

    @classmethod
    def get_local_ip(cls) -> str:
        """로컬 IP 주소 가져오기 (실제 LAN IP 우선)

        순수 Python 방식 - PowerShell/cmd 없이 동작 (경량 Windows 대응)

        우선순위:
        1. 192.168.x.x, 10.x.x.x (일반 LAN)
        2. 172.16-31.x.x (사설 네트워크)
        3. 기타 (Tailscale 100.x, APIPA 169.254.x 등은 후순위)
        """
        try:
            all_ips = cls._get_all_ipv4_addresses()

            if all_ips:
                # 우선순위별 정렬
                all_ips.sort(key=cls._classify_ip)
                best_ip = all_ips[0]
                print(f"[Network] IP 감지: {best_ip} (전체: {all_ips})")
                return best_ip

        except Exception as e:
            print(f"[Network] IP 감지 오류: {e}")

        # 최종 폴백: 기존 방식
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "192.168.0.1"

    @staticmethod
    def get_network_range(local_ip: str, prefix: int = 24) -> List[str]:
        """로컬 IP 기반 네트워크 범위 생성"""
        try:
            network = ipaddress.IPv4Network(f"{local_ip}/{prefix}", strict=False)
            return [str(ip) for ip in network.hosts()]
        except Exception:
            # 기본 범위
            base = ".".join(local_ip.split(".")[:3])
            return [f"{base}.{i}" for i in range(1, 255)]

    @staticmethod
    def check_kvm_device(ip: str, port: int = 80, timeout: float = 1.5) -> Optional[DiscoveredDevice]:
        """단일 IP에서 KVM 장치 확인"""
        try:
            # TCP 포트 열림 확인 (빠른 체크)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((ip, port))
            sock.close()

            if result != 0:
                return None

            # HTTP 요청으로 PicoKVM 확인
            url = f"http://{ip}:{port}/"
            response = requests.get(url, timeout=timeout)

            if response.status_code == 200:
                content = response.text.lower()

                # PicoKVM/Luckfox 키워드 확인
                is_kvm = any(kw in content for kw in [
                    'kvm', 'luckfox', 'pikvm', 'pico', 'stream', 'webrtc'
                ])

                if is_kvm or 'title>kvm' in content:
                    device = DiscoveredDevice(
                        ip=ip,
                        port=port,
                        name=f"KVM-{ip.split('.')[-1]}",
                        model="Luckfox PicoKVM",
                        is_online=True
                    )

                    # 추가 정보 수집 시도
                    try:
                        info_url = f"http://{ip}:{port}/api/info"
                        info_resp = requests.get(info_url, timeout=1)
                        if info_resp.status_code == 200:
                            info = info_resp.json()
                            device.version = info.get('version', '')
                            device.mac = info.get('mac', '')
                            if info.get('hostname'):
                                device.name = info['hostname']
                    except Exception:
                        pass

                    return device

            return None

        except Exception:
            return None

    @classmethod
    def scan_network(
        cls,
        ip_range: Optional[List[str]] = None,
        ports: Optional[List[int]] = None,
        max_workers: int = 50,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> List[DiscoveredDevice]:
        """
        네트워크 스캔하여 KVM 장치 찾기

        Args:
            ip_range: 스캔할 IP 목록 (None이면 자동 감지)
            ports: 스캔할 포트 목록
            max_workers: 동시 스캔 스레드 수
            progress_callback: 진행 콜백 (current, total)

        Returns:
            발견된 KVM 장치 목록
        """
        if ip_range is None:
            local_ip = cls.get_local_ip()
            ip_range = cls.get_network_range(local_ip)

        if ports is None:
            ports = cls.DEFAULT_PORTS

        discovered = []
        total = len(ip_range) * len(ports)
        current = 0

        # 이미 발견된 IP 추적 (중복 방지)
        found_ips = set()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}

            for ip in ip_range:
                for port in ports:
                    future = executor.submit(cls.check_kvm_device, ip, port, cls.TIMEOUT)
                    futures[future] = (ip, port)

            for future in as_completed(futures):
                current += 1
                ip, port = futures[future]

                if progress_callback:
                    progress_callback(current, total)

                try:
                    device = future.result()
                    if device and device.ip not in found_ips:
                        found_ips.add(device.ip)
                        discovered.append(device)
                except Exception:
                    pass

        return discovered


class DiscoveryThread(QThread):
    """Qt 스레드 기반 자동 검색"""

    # 시그널 정의
    device_found = pyqtSignal(object)  # DiscoveredDevice
    progress_updated = pyqtSignal(int, int)  # current, total
    scan_completed = pyqtSignal(list)  # List[DiscoveredDevice]
    scan_error = pyqtSignal(str)  # error message

    def __init__(self,
                 ip_range: Optional[List[str]] = None,
                 ports: Optional[List[int]] = None,
                 parent=None):
        super().__init__(parent)
        self.ip_range = ip_range
        self.ports = ports
        self._is_running = True
        self._executor = None

    def run(self):
        """스캔 실행"""
        try:
            if self.ip_range is None:
                local_ip = NetworkScanner.get_local_ip()
                self.ip_range = NetworkScanner.get_network_range(local_ip)

            if self.ports is None:
                self.ports = NetworkScanner.DEFAULT_PORTS

            discovered = []
            total = len(self.ip_range) * len(self.ports)
            current = 0
            found_ips = set()

            self._executor = ThreadPoolExecutor(max_workers=50)
            try:
                futures = {}

                for ip in self.ip_range:
                    if not self._is_running:
                        break
                    for port in self.ports:
                        future = self._executor.submit(
                            NetworkScanner.check_kvm_device,
                            ip, port,
                            NetworkScanner.TIMEOUT
                        )
                        futures[future] = (ip, port)

                for future in as_completed(futures):
                    if not self._is_running:
                        # 남은 future 모두 취소
                        for f in futures:
                            f.cancel()
                        break

                    current += 1
                    self.progress_updated.emit(current, total)

                    try:
                        device = future.result(timeout=0.1)
                        if device and device.ip not in found_ips:
                            found_ips.add(device.ip)
                            discovered.append(device)
                            self.device_found.emit(device)
                    except Exception:
                        pass
            finally:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None

            if self._is_running:
                self.scan_completed.emit(discovered)

        except Exception as e:
            if self._is_running:
                self.scan_error.emit(str(e))

    def stop(self):
        """스캔 중지 - executor도 즉시 종료"""
        self._is_running = False
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)


class AutoDiscoveryManager(QObject):
    """자동 검색 관리자 - 주기적 스캔 및 장치 관리"""

    # 시그널
    new_device_found = pyqtSignal(object)  # DiscoveredDevice
    device_lost = pyqtSignal(str)  # IP
    scan_started = pyqtSignal()
    scan_finished = pyqtSignal(int)  # 발견된 장치 수

    def __init__(self, parent=None):
        super().__init__(parent)
        self._known_devices: Dict[str, DiscoveredDevice] = {}
        self._scan_thread: Optional[DiscoveryThread] = None
        self._auto_scan_enabled = False

    @property
    def known_devices(self) -> List[DiscoveredDevice]:
        """알려진 장치 목록"""
        return list(self._known_devices.values())

    def start_scan(self,
                   ip_range: Optional[List[str]] = None,
                   ports: Optional[List[int]] = None):
        """스캔 시작"""
        if self._scan_thread and self._scan_thread.isRunning():
            return  # 이미 스캔 중

        self._scan_thread = DiscoveryThread(ip_range, ports, self)
        self._scan_thread.device_found.connect(self._on_device_found)
        self._scan_thread.scan_completed.connect(self._on_scan_completed)
        self._scan_thread.start()

        self.scan_started.emit()

    def stop_scan(self):
        """스캔 중지"""
        if self._scan_thread:
            self._scan_thread.stop()
            self._scan_thread.wait()

    def _on_device_found(self, device: DiscoveredDevice):
        """장치 발견 시"""
        if device.ip not in self._known_devices:
            self._known_devices[device.ip] = device
            self.new_device_found.emit(device)

    def _on_scan_completed(self, devices: List[DiscoveredDevice]):
        """스캔 완료 시"""
        # 사라진 장치 감지
        current_ips = {d.ip for d in devices}
        for ip in list(self._known_devices.keys()):
            if ip not in current_ips:
                del self._known_devices[ip]
                self.device_lost.emit(ip)

        self.scan_finished.emit(len(devices))

    def is_scanning(self) -> bool:
        """스캔 중인지 확인"""
        return self._scan_thread is not None and self._scan_thread.isRunning()
