"""
네트워크 우선순위 자동 조정 모듈
Tailscale(100.x) / APIPA(169.254.x)가 기본 라우트인 경우
LAN(192.168.x / 10.x)을 우선으로 변경

순수 Python으로 동작 - PowerShell/cmd 차단된 경량 Windows에서도 작동
"""

import subprocess
import sys
import os
import ctypes
import logging
import threading
import time

logger = logging.getLogger(__name__)


def _tailscale_exe() -> str:
    """Tailscale CLI 경로 반환 (PATH에 없어도 동작)"""
    for path in [
        os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'Tailscale', 'tailscale.exe'),
        os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'), 'Tailscale', 'tailscale.exe'),
    ]:
        if os.path.isfile(path):
            return path
    return 'tailscale'


def is_admin() -> bool:
    """관리자 권한 확인"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def get_default_route_ip() -> str:
    """현재 기본 라우트(인터넷)로 나가는 IP 확인"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def needs_network_fix() -> bool:
    """네트워크 수정이 필요한지 확인

    기본 라우트가 100.x(Tailscale) 또는 169.254.x(APIPA)이면 True
    """
    default_ip = get_default_route_ip()
    if not default_ip:
        return False

    # 기본 라우트가 이미 LAN이면 수정 불필요
    if default_ip.startswith('192.168.') or default_ip.startswith('10.'):
        return False

    # 172.16-31 사설 대역도 OK
    if default_ip.startswith('172.'):
        parts = default_ip.split('.')
        try:
            if 16 <= int(parts[1]) <= 31:
                return False
        except (IndexError, ValueError):
            pass

    # 100.x(Tailscale) 또는 169.254.x(APIPA)이면 수정 필요
    logger.info(f"[NetworkFix] 기본 라우트가 LAN이 아님: {default_ip}")
    return True


def _get_interface_ip_map() -> dict:
    """netsh로 인터페이스 이름 -> IP 매핑 조회

    Returns:
        dict: {인터페이스이름: [ip1, ip2, ...]}
    """
    mapping = {}
    try:
        result = subprocess.run(
            ['netsh', 'interface', 'ipv4', 'show', 'addresses'],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000
        )
        if result.returncode != 0:
            return mapping

        current_iface = None
        for line in result.stdout.split('\n'):
            line = line.rstrip()
            # 인터페이스 이름 감지: "인터페이스 ..." 또는 "Configuration for interface ..."
            if 'interface' in line.lower() or '\uc778\ud130\ud398\uc774\uc2a4' in line:
                # 따옴표 안의 이름 추출
                if '"' in line:
                    parts = line.split('"')
                    if len(parts) >= 2:
                        current_iface = parts[1]
                elif ':' in line:
                    current_iface = line.split(':')[-1].strip().strip('"')
            # IP 주소 행 감지
            elif current_iface and ('IP' in line or 'ip' in line) and '.' in line:
                # "   IP 주소:                           192.168.0.100"
                for part in line.split():
                    if '.' in part and part[0].isdigit():
                        if current_iface not in mapping:
                            mapping[current_iface] = []
                        mapping[current_iface].append(part)
                        break
    except Exception as e:
        logger.warning(f"[NetworkFix] 인터페이스 IP 매핑 실패: {e}")

    return mapping


def _find_tailscale_interface_name() -> str:
    """실제 Tailscale 네트워크 인터페이스 이름 찾기

    Windows에서 Tailscale 인터페이스 이름이 'Tailscale', 'Tailscale 2' 등
    다양할 수 있으므로 100.x IP를 가진 인터페이스를 직접 찾음.

    Returns:
        인터페이스 이름 (못 찾으면 'Tailscale' 폴백)
    """
    # 방법 1: netsh interface ipv4 show addresses 파싱
    iface_map = _get_interface_ip_map()
    for iface_name, ips in iface_map.items():
        for ip in ips:
            if ip.startswith('100.'):
                logger.info(f"[NetworkFix] Tailscale 인터페이스 발견: '{iface_name}' ({ip})")
                return iface_name

    # 방법 2: 이름에 'tailscale' 포함된 인터페이스 찾기
    try:
        result = subprocess.run(
            ['netsh', 'interface', 'ipv4', 'show', 'interfaces'],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'tailscale' in line.lower():
                    # 마지막 컬럼이 인터페이스 이름
                    parts = line.strip().split()
                    if len(parts) >= 4:
                        # Idx Met MTU State Name (Name은 공백 포함 가능)
                        name = ' '.join(parts[4:]) if len(parts) > 4 else parts[-1]
                        logger.info(f"[NetworkFix] Tailscale 인터페이스 (이름 매칭): '{name}'")
                        return name
    except Exception:
        pass

    return 'Tailscale'


def _find_lan_interface_names() -> list:
    """LAN 인터페이스 이름들 찾기 (192.168.x, 10.x 가진 어댑터)

    Returns:
        list of 인터페이스 이름
    """
    lan_names = []
    iface_map = _get_interface_ip_map()
    for iface_name, ips in iface_map.items():
        for ip in ips:
            if ip.startswith('192.168.') or ip.startswith('10.'):
                if iface_name not in lan_names:
                    lan_names.append(iface_name)
                    logger.info(f"[NetworkFix] LAN 인터페이스 발견: '{iface_name}' ({ip})")
                break
    return lan_names


def fix_network_priority_netsh() -> bool:
    """netsh로 네트워크 메트릭 조정 (관리자 권한 필요)

    하드코딩 대신 실제 인터페이스 IP를 파싱하여 정확한 이름을 찾음.

    Returns:
        True: 성공, False: 실패
    """
    try:
        # netsh 사용 가능 여부 확인
        result = subprocess.run(
            ['netsh', 'interface', 'ipv4', 'show', 'interfaces'],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000  # CREATE_NO_WINDOW
        )
        if result.returncode != 0:
            logger.warning("[NetworkFix] netsh 사용 불가")
            return False

        success = False

        # 1. Tailscale 인터페이스 찾기 -> 메트릭 1000 (낮은 우선순위)
        ts_name = _find_tailscale_interface_name()
        r = subprocess.run(
            ['netsh', 'interface', 'ipv4', 'set', 'interface', ts_name, 'metric=1000'],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000
        )
        if r.returncode == 0:
            logger.info(f"[NetworkFix] '{ts_name}' metric=1000 설정 완료")
            success = True
        else:
            logger.warning(f"[NetworkFix] '{ts_name}' metric 설정 실패: {r.stderr.strip()}")

        # 2. LAN 인터페이스 찾기 -> 메트릭 5 (높은 우선순위)
        lan_names = _find_lan_interface_names()
        for name in lan_names:
            r = subprocess.run(
                ['netsh', 'interface', 'ipv4', 'set', 'interface', name, 'metric=5'],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000
            )
            if r.returncode == 0:
                logger.info(f"[NetworkFix] '{name}' metric=5 설정 완료")
                success = True
            else:
                logger.warning(f"[NetworkFix] '{name}' metric=5 실패: {r.stderr.strip()}")

        return success

    except Exception as e:
        logger.error(f"[NetworkFix] netsh 실행 오류: {e}")
        return False


def fix_network_priority_wmi() -> bool:
    """WMI로 네트워크 메트릭 조정 (Python 전용, cmd/netsh 불필요)

    Returns:
        True: 성공, False: 실패
    """
    try:
        import wmi
        c = wmi.WMI()

        # 모든 네트워크 어댑터 설정 가져오기
        adapters = c.Win32_NetworkAdapterConfiguration(IPEnabled=True)

        for adapter in adapters:
            if not adapter.IPAddress:
                continue

            ip = adapter.IPAddress[0]
            desc = adapter.Description or ""

            if ip.startswith('192.168.') or ip.startswith('10.'):
                # LAN 어댑터 - 높은 우선순위
                try:
                    adapter.SetIPConnectionMetric(5)
                    logger.info(f"[NetworkFix/WMI] {desc} ({ip}) metric=5")
                except Exception:
                    pass

            elif ip.startswith('100.') or 'tailscale' in desc.lower():
                # Tailscale - 낮은 우선순위
                try:
                    adapter.SetIPConnectionMetric(1000)
                    logger.info(f"[NetworkFix/WMI] {desc} ({ip}) metric=1000")
                except Exception:
                    pass

            elif ip.startswith('169.254.'):
                # APIPA - 최저 우선순위
                try:
                    adapter.SetIPConnectionMetric(2000)
                    logger.info(f"[NetworkFix/WMI] {desc} ({ip}) metric=2000")
                except Exception:
                    pass

        return True

    except ImportError:
        logger.warning("[NetworkFix] WMI 모듈 없음 (pip install wmi)")
        return False
    except Exception as e:
        logger.error(f"[NetworkFix] WMI 오류: {e}")
        return False


def enable_ip_forwarding() -> bool:
    """Windows IP Forwarding 활성화 (관제 PC에서 KVM 서브넷 라우팅)

    Tailscale 서브넷 라우팅을 통해 원격 PC가 관제 PC의 KVM 서브넷에 접근하려면
    관제 PC의 Windows에서 IP Forwarding이 활성화되어야 합니다.

    1단계: 레지스트리 (재부팅 후 영구 적용)
    2단계: netsh interface forwarding=enabled (즉시 적용, 재부팅 불필요)
    """
    if not is_admin():
        return False

    try:
        # 현재 레지스트리 상태 확인
        result = subprocess.run(
            ['reg', 'query',
             r'HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters',
             '/v', 'IPEnableRouter'],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000
        )

        registry_ok = '0x1' in result.stdout

        if not registry_ok:
            # 레지스트리 설정 (재부팅 후 영구 적용)
            result = subprocess.run(
                ['reg', 'add',
                 r'HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters',
                 '/v', 'IPEnableRouter', '/t', 'REG_DWORD', '/d', '1', '/f'],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000
            )
            if result.returncode == 0:
                logger.info("[IPForward] 레지스트리 IP Forwarding 설정 완료")
                registry_ok = True

            # RemoteAccess 서비스 시작 시도
            subprocess.run(
                ['sc', 'config', 'RemoteAccess', 'start=', 'auto'],
                capture_output=True, timeout=5,
                creationflags=0x08000000
            )
            subprocess.run(
                ['net', 'start', 'RemoteAccess'],
                capture_output=True, timeout=5,
                creationflags=0x08000000
            )
        else:
            logger.info("[IPForward] 레지스트리 이미 활성화됨")

        # 런타임 forwarding 활성화 (즉시 적용, 재부팅 불필요)
        _enable_runtime_forwarding()

        return registry_ok

    except Exception as e:
        logger.error(f"[IPForward] 오류: {e}")

    return False


def _enable_runtime_forwarding():
    """netsh로 인터페이스별 IP forwarding 즉시 활성화

    레지스트리만으로는 재부팅이 필요하지만,
    netsh interface ipv4 set interface <idx> forwarding=enabled 는 즉시 적용됨.
    """
    try:
        # 인터페이스 목록 조회
        result = subprocess.run(
            ['netsh', 'interface', 'ipv4', 'show', 'interfaces'],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000
        )
        if result.returncode != 0:
            return

        # 활성 인터페이스의 Idx 추출 (connected 상태)
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if not line or 'connected' not in line.lower():
                continue

            parts = line.split()
            if len(parts) < 4:
                continue

            try:
                idx = int(parts[0])
            except ValueError:
                continue

            # 각 인터페이스에 forwarding 활성화
            r = subprocess.run(
                ['netsh', 'interface', 'ipv4', 'set', 'interface',
                 str(idx), 'forwarding=enabled'],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000
            )
            if r.returncode == 0:
                # 인터페이스 이름 추출 (마지막 컬럼)
                name = parts[-1] if len(parts) > 4 else str(idx)
                logger.info(f"[IPForward] interface {idx} ({name}) forwarding=enabled")

    except Exception as e:
        logger.warning(f"[IPForward] 런타임 forwarding 설정 오류: {e}")


def setup_tailscale_subnet_route(tailscale_ip: str, lan_ip: str):
    """Tailscale 서브넷 라우팅 설정

    관제 PC에서 실행: tailscale up --advertise-routes=<subnet>
    원격 PC가 이 PC의 KVM 서브넷에 접근할 수 있도록 함.

    Args:
        tailscale_ip: 이 PC의 Tailscale IP (예: 100.64.0.2)
        lan_ip: 이 PC의 LAN IP (예: 192.168.68.x)
    """
    try:
        parts = lan_ip.split('.')
        subnet = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"

        import subprocess
        # Tailscale에 서브넷 라우팅 광고
        r = subprocess.run(
            [_tailscale_exe(), 'up', f'--advertise-routes={subnet}', '--accept-routes'],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000
        )
        if r.returncode == 0:
            logger.info(f"[Tailscale] 서브넷 라우팅 광고: {subnet} (via {tailscale_ip})")
        else:
            logger.warning(f"[Tailscale] 서브넷 라우팅 설정 실패: {r.stderr.strip()}")
    except Exception as e:
        logger.warning(f"[Tailscale] route 설정 실패 (무시): {e}")


def setup_relay_firewall_rules():
    """KVM 릴레이 포트에 대한 Windows 방화벽 규칙 추가

    TCP 18000-19999 (KVM TCP 프록시)
    UDP 28000-29999 (WebRTC 미디어 릴레이)
    """
    if not is_admin():
        return

    rules = [
        ('WellcomLAND-Relay-TCP', 'TCP', '18000-19999'),
        ('WellcomLAND-Relay-UDP', 'UDP', '28000-29999'),
    ]

    for name, proto, ports in rules:
        try:
            # 기존 규칙 제거 후 재생성
            subprocess.run(
                ['netsh', 'advfirewall', 'firewall', 'delete', 'rule',
                 f'name={name}'],
                capture_output=True, timeout=5,
                creationflags=0x08000000
            )
            result = subprocess.run(
                ['netsh', 'advfirewall', 'firewall', 'add', 'rule',
                 f'name={name}', 'dir=in', 'action=allow',
                 f'protocol={proto}', f'localport={ports}'],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000
            )
            if result.returncode == 0:
                logger.info(f"[Firewall] {name} ({proto} {ports}) 규칙 추가")
        except Exception as e:
            logger.warning(f"[Firewall] {name} 규칙 추가 실패: {e}")


def auto_setup_tailscale_forwarding(tailscale_ip: str = "", lan_ip: str = ""):
    """관제 PC에서 Tailscale 서브넷 라우팅 자동 설정

    1. IP Forwarding 활성화 (레지스트리 + 런타임)
    2. 방화벽 규칙 추가 (릴레이 포트)
    3. Tailscale 서브넷 라우팅 광고
    """
    if not tailscale_ip or not lan_ip:
        return

    # Tailscale IP가 아닌 일반 LAN IP여야 의미 있음
    if lan_ip.startswith('100.') or lan_ip.startswith('169.254.'):
        return

    logger.info(f"[Tailscale] 관제 PC 서브넷 라우팅 설정: LAN={lan_ip} TS={tailscale_ip}")

    # 1. IP Forwarding 활성화 (레지스트리 + 런타임 netsh)
    enable_ip_forwarding()

    # 2. 방화벽 규칙 추가
    setup_relay_firewall_rules()

    # 3. Tailscale 서브넷 라우팅 광고
    setup_tailscale_subnet_route(tailscale_ip, lan_ip)

    # 4. 원격 PC에서 accept-routes 활성화
    try:
        import subprocess
        subprocess.run(
            [_tailscale_exe(), 'up', '--accept-routes'],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000
        )
    except Exception:
        pass


def _ensure_tailscale_high_metric():
    """Tailscale 인터페이스 메트릭을 항상 높게 설정

    Tailscale이 기본 라우트를 빼앗지 않도록 메트릭을 1000으로 설정.
    LAN이 이미 기본 라우트여도 매번 실행 -- 재부팅/Tailscale 재시작 시 리셋 방지.

    인터페이스 이름을 하드코딩하지 않고 100.x IP로 자동 감지
    """
    if not is_admin():
        return

    try:
        ts_name = _find_tailscale_interface_name()

        # Tailscale 인터페이스 메트릭 올리기 (이미 1000이면 무시됨)
        result = subprocess.run(
            ['netsh', 'interface', 'ipv4', 'set', 'interface', ts_name, 'metric=1000'],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000
        )
        if result.returncode == 0:
            logger.info(f"[NetworkFix] '{ts_name}' metric=1000 설정 완료 (LAN 우선)")

        # LAN 인터페이스도 함께 metric=5로 설정 (재부팅 후 자동 리셋 방지)
        lan_names = _find_lan_interface_names()
        for name in lan_names:
            subprocess.run(
                ['netsh', 'interface', 'ipv4', 'set', 'interface', name, 'metric=5'],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000
            )
    except Exception as e:
        logger.warning(f"[NetworkFix] Tailscale metric 설정 실패: {e}")


def auto_fix_network():
    """앱 시작 시 자동 네트워크 우선순위 조정 + Tailscale 자동 등록

    1. Tailscale 메트릭을 항상 높게 설정 (매번 실행)
    2. 기본 라우트가 LAN이 아닌 경우 추가 수정
    3. Tailscale tailnet 미참여 시 authkey로 자동 등록 (인스톨러 대신)
    """
    # === 1단계: 네트워크 메트릭 조정 ===
    try:
        # Tailscale 메트릭은 항상 높게 유지 (기본 라우트 상관없이)
        _ensure_tailscale_high_metric()

        if not needs_network_fix():
            logger.info("[NetworkFix] 네트워크 정상 (LAN이 기본 라우트)")
        elif not is_admin():
            logger.info("[NetworkFix] 관리자 권한 없음 - 메트릭 변경 건너뜀")
        else:
            default_ip = get_default_route_ip()
            logger.info(f"[NetworkFix] 기본 라우트 IP: {default_ip} - 수정 필요")

            # 방법 1: netsh (가장 일반적)
            if fix_network_priority_netsh():
                new_ip = get_default_route_ip()
                logger.info(f"[NetworkFix] netsh로 수정 완료. 새 기본 라우트: {new_ip}")
            # 방법 2: WMI (cmd/netsh 차단된 경우)
            elif fix_network_priority_wmi():
                new_ip = get_default_route_ip()
                logger.info(f"[NetworkFix] WMI로 수정 완료. 새 기본 라우트: {new_ip}")
            else:
                logger.warning("[NetworkFix] 자동 수정 실패")

    except Exception as e:
        logger.error(f"[NetworkFix] 오류: {e}")

    # === 2단계: Tailscale 자동 등록 (인스톨러 대신 앱 시작 시 처리) ===
    try:
        _ensure_tailscale_joined()
    except Exception as e:
        logger.error(f"[Tailscale] 자동 등록 오류 (무시): {e}")

    # === 3단계: Tailscale 워치독 시작 (런타임 중 강제 종료 대응) ===
    try:
        start_tailscale_watchdog()
    except Exception as e:
        logger.warning(f"[Tailscale] 워치독 시작 실패 (무시): {e}")


_last_join_time: float = 0  # 마지막 tailscale join 시도 시각
_JOIN_COOLDOWN = 60  # 재시도 쿨다운 (초)


def _ensure_tailscale_joined():
    """Tailscale 서비스 확인 + tailnet 참여 확인 + 미참여 시 authkey로 자동 등록

    WellcomLAND 실행할 때마다 호출됨 (설치 시에만이 아님)
    - Tailscale 서비스 꺼져 있으면 자동 시작
    - tailnet 미참여(NeedsLogin 등)이면 authkey로 자동 등록
    - 이미 참여 중이면 accept-routes 갱신만
    - 쿨다운: 60초 이내 재호출 시 건너뜀 (빠른 재시작 시 연결 리셋 방지)
    """
    global _last_join_time
    import json as _json

    # 쿨다운: 마지막 실행 후 60초 이내면 건너뜀
    now = time.time()
    if now - _last_join_time < _JOIN_COOLDOWN:
        elapsed = int(now - _last_join_time)
        logger.info(f"[Tailscale] 쿨다운 중 ({elapsed}초 전 실행) - 건너뜀")
        return
    _last_join_time = now

    exe = _tailscale_exe()

    # 1. Tailscale 설치 확인
    try:
        r = subprocess.run(
            [exe, 'version'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=5, creationflags=0x08000000
        )
        if r.returncode != 0:
            logger.info("[Tailscale] 미설치 - 건너뜀")
            return
    except FileNotFoundError:
        logger.info("[Tailscale] 미설치 - 건너뜀")
        return

    # 2. Tailscale 서비스 상태 확인 + 자동 시작
    _ensure_tailscale_service_running()

    # 3. 상태 확인 (서비스 시작 직후일 수 있으므로 재시도)
    backend_state = ''
    for attempt in range(3):
        r = subprocess.run(
            [exe, 'status', '--json'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=10, creationflags=0x08000000
        )

        json_text = r.stdout.strip() or r.stderr.strip()
        if json_text:
            try:
                status = _json.loads(json_text)
                backend_state = status.get('BackendState', '')
                if backend_state:
                    break
            except Exception:
                pass

        if attempt < 2:
            time.sleep(2)  # 서비스 초기화 대기

    logger.info(f"[Tailscale] 시작 시 상태: {backend_state}")

    # 3. 이미 참여 중이면 accept-routes만 보장
    if backend_state == 'Running':
        # accept-routes + advertise-routes 갱신
        lan_subnet = _detect_lan_subnet_simple()
        cmd = [exe, 'up', '--accept-routes']
        if lan_subnet:
            cmd.append(f'--advertise-routes={lan_subnet}')
        subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=15, creationflags=0x08000000
        )
        logger.info(f"[Tailscale] 이미 참여 중 - accept-routes 갱신"
                     + (f", advertise={lan_subnet}" if lan_subnet else ""))
        return

    # 4. 미참여 (NeedsLogin, NoState, Stopped 등) -> authkey로 자동 참여
    if backend_state not in ('NeedsLogin', 'NoState', 'Stopped', ''):
        return  # 예상 밖 상태

    logger.info("[Tailscale] tailnet 미참여 - authkey로 자동 참여 시도")

    # authkey 획득: 로컬 캐시 -> 기본값
    from config import settings
    authkey = settings.get('tailscale.authkey', '')
    if not authkey:
        # 기본 authkey (회사 tailnet용)
        authkey = 'tskey-auth-kyaZwNxLUa11CNTRL-pFBEvigZ5m2REQRrSiE4m211EnJ4JxbJ'

    if not authkey:
        logger.warning("[Tailscale] authkey 없음 - 수동 로그인 필요")
        return

    # tailscale up --authkey=... --accept-routes (--reset 제거: 연결 리셋 방지)
    lan_subnet = _detect_lan_subnet_simple()
    cmd = [exe, 'up', f'--authkey={authkey}', '--accept-routes']
    if lan_subnet:
        cmd.append(f'--advertise-routes={lan_subnet}')
        logger.info(f"[Tailscale] 서브넷 광고 예정: {lan_subnet}")

    r = subprocess.run(
        cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=30, creationflags=0x08000000
    )
    if r.returncode == 0:
        logger.info("[Tailscale] authkey로 tailnet 참여 완료!")
        settings.set('tailscale.joined', True)
        settings.set('tailscale.authkey', authkey)
    else:
        err_msg = r.stderr.strip() or r.stdout.strip()
        logger.error(f"[Tailscale] authkey 참여 실패: {err_msg}")
        # --reset 없이 실패하면 한번만 --reset으로 재시도
        if 'already' in err_msg.lower() or 'preferences' in err_msg.lower():
            logger.info("[Tailscale] 기존 설정 충돌 - --reset으로 재시도")
            cmd_reset = [exe, 'up', f'--authkey={authkey}', '--accept-routes', '--reset']
            if lan_subnet:
                cmd_reset.append(f'--advertise-routes={lan_subnet}')
            r2 = subprocess.run(
                cmd_reset, capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=30, creationflags=0x08000000
            )
            if r2.returncode == 0:
                logger.info("[Tailscale] --reset으로 tailnet 참여 완료!")
                settings.set('tailscale.joined', True)
                settings.set('tailscale.authkey', authkey)
            else:
                logger.error(f"[Tailscale] --reset도 실패: {r2.stderr.strip() or r2.stdout.strip()}")


def _ensure_tailscale_service_running():
    """Tailscale 서비스(Tailscale)가 중지되어 있으면 자동 시작

    WellcomLAND 실행 시 Tailscale 서비스가 꺼져 있는 경우 대응.
    Windows 서비스명: Tailscale
    """
    try:
        # sc query로 서비스 상태 확인
        r = subprocess.run(
            ['sc', 'query', 'Tailscale'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=5, creationflags=0x08000000
        )

        output = r.stdout + r.stderr
        if 'RUNNING' in output:
            logger.info("[Tailscale] 서비스 실행 중")
            return

        if 'STOPPED' in output or 'STOP_PENDING' in output:
            logger.warning("[Tailscale] 서비스 중지됨 - 자동 시작 시도")
            sr = subprocess.run(
                ['sc', 'start', 'Tailscale'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=10, creationflags=0x08000000
            )
            if sr.returncode == 0:
                logger.info("[Tailscale] 서비스 시작 명령 전송 완료")
                # 서비스 초기화 대기
                time.sleep(5)
            else:
                logger.warning(f"[Tailscale] 서비스 시작 실패: {sr.stderr.strip()}")
                # net start로 재시도
                sr2 = subprocess.run(
                    ['net', 'start', 'Tailscale'],
                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                    timeout=15, creationflags=0x08000000
                )
                if sr2.returncode == 0:
                    logger.info("[Tailscale] net start로 서비스 시작 성공")
                    time.sleep(5)
                else:
                    logger.error(f"[Tailscale] net start도 실패: {sr2.stderr.strip()}")

        elif r.returncode != 0:
            logger.warning(f"[Tailscale] 서비스 조회 실패 (미설치?): {output.strip()}")

    except Exception as e:
        logger.error(f"[Tailscale] 서비스 확인 오류: {e}")

    # 트레이(GUI) 프로세스도 확인 — 없으면 자동 실행
    _ensure_tailscale_tray_running()


def _ensure_tailscale_tray_running():
    """Tailscale 트레이(GUI) 프로세스가 없으면 자동 실행

    서비스만으로 VPN은 동작하지만, 트레이가 없으면
    사용자가 Tailscale 상태를 확인할 수 없고 불안해함.
    작업표시줄에 아이콘이 보이도록 트레이 앱을 자동 시작.
    """
    try:
        # tasklist로 tailscale-ipn.exe (트레이 GUI) 확인
        r = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq tailscale-ipn.exe', '/NH'],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=5, creationflags=0x08000000
        )
        if 'tailscale-ipn.exe' in r.stdout.lower():
            return  # 이미 실행 중

        # 트레이 앱 경로 찾기
        tray_paths = [
            os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'),
                         'Tailscale', 'tailscale-ipn.exe'),
            os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
                         'Tailscale', 'tailscale-ipn.exe'),
        ]
        tray_exe = None
        for p in tray_paths:
            if os.path.isfile(p):
                tray_exe = p
                break

        if not tray_exe:
            return  # 트레이 앱 미설치

        logger.info(f"[Tailscale] 트레이 앱 자동 실행: {tray_exe}")
        subprocess.Popen(
            [tray_exe],
            creationflags=0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS
        )

    except Exception as e:
        logger.warning(f"[Tailscale] 트레이 앱 실행 실패 (무시): {e}")


def _detect_lan_subnet_simple() -> str:
    """현재 PC의 LAN 서브넷 감지 (Tailscale/APIPA 제외, 순수 Python)

    Returns:
        "192.168.0.0/24" 형태, 없으면 빈 문자열
    """
    import socket
    try:
        hostname = socket.gethostname()
        ips = socket.getaddrinfo(hostname, None, socket.AF_INET)
        subnets = []
        for info in ips:
            ip = info[4][0]
            if ip.startswith('100.') or ip.startswith('169.254.') or ip.startswith('127.'):
                continue
            parts = ip.split('.')
            if len(parts) == 4:
                subnet = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
                if subnet not in subnets:
                    subnets.append(subnet)
        return ','.join(subnets) if subnets else ''
    except Exception:
        return ''


# ============================================================
# Tailscale Watchdog - 런타임 중 Tailscale 강제 종료 시 자동 복구
# ============================================================

_watchdog_thread: threading.Thread = None
_watchdog_running = False


def start_tailscale_watchdog(interval: int = 30):
    """Tailscale 상태를 주기적으로 감시, 죽으면 자동 복구

    Args:
        interval: 감시 주기 (초), 기본 30초
    """
    global _watchdog_thread, _watchdog_running

    if _watchdog_running:
        return  # 이미 실행 중

    _watchdog_running = True
    _watchdog_thread = threading.Thread(
        target=_tailscale_watchdog_loop,
        args=(interval,),
        daemon=True,
        name='TailscaleWatchdog'
    )
    _watchdog_thread.start()
    logger.info(f"[Tailscale] 워치독 시작 (감시 주기: {interval}초)")


def _tailscale_watchdog_loop(interval: int):
    """Tailscale 워치독 루프

    30초마다 Tailscale 서비스 + 연결 상태 확인.
    서비스 죽으면 재시작, 로그아웃되면 재인증.
    """
    global _watchdog_running
    import json as _json

    exe = _tailscale_exe()
    consecutive_failures = 0

    while _watchdog_running:
        try:
            time.sleep(interval)

            # 1. 서비스 상태 확인
            r = subprocess.run(
                ['sc', 'query', 'Tailscale'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=5, creationflags=0x08000000
            )
            output = r.stdout + r.stderr

            if 'RUNNING' not in output:
                consecutive_failures += 1
                logger.warning(f"[Watchdog] Tailscale 서비스 비정상 (연속 {consecutive_failures}회)")

                if 'STOPPED' in output or 'STOP_PENDING' in output:
                    logger.info("[Watchdog] 서비스 재시작 시도")
                    _ensure_tailscale_service_running()
                    time.sleep(5)

                    # 재시작 후 재인증 필요할 수 있음 (쿨다운 리셋 후 재시도)
                    global _last_join_time
                    _last_join_time = 0  # 워치독 복구는 쿨다운 무시
                    _ensure_tailscale_joined()
                continue

            # 2. Tailscale 연결 상태 확인
            r = subprocess.run(
                [exe, 'status', '--json'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=10, creationflags=0x08000000
            )
            json_text = r.stdout.strip() or r.stderr.strip()
            backend_state = ''
            if json_text:
                try:
                    status = _json.loads(json_text)
                    backend_state = status.get('BackendState', '')
                except Exception:
                    pass

            if backend_state == 'Running':
                # 정상
                if consecutive_failures > 0:
                    logger.info(f"[Watchdog] Tailscale 복구 확인 (이전 {consecutive_failures}회 실패)")
                consecutive_failures = 0
            elif backend_state in ('NeedsLogin', 'NoState', 'Stopped'):
                consecutive_failures += 1
                logger.warning(f"[Watchdog] Tailscale 상태 비정상: {backend_state} - 재인증 시도")
                _last_join_time = 0  # 워치독 복구는 쿨다운 무시
                _ensure_tailscale_joined()
            else:
                # Starting 등 과도기 상태 - 다음 체크까지 대기
                pass

        except Exception as e:
            logger.error(f"[Watchdog] 오류: {e}")
            consecutive_failures += 1

        # 연속 실패가 너무 많으면 간격 늘리기 (부하 방지)
        if consecutive_failures > 10:
            time.sleep(interval * 2)


def stop_tailscale_watchdog():
    """워치독 중지"""
    global _watchdog_running
    _watchdog_running = False
    logger.info("[Tailscale] 워치독 중지")
