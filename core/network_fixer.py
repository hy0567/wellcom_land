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
    print(f"[NetworkFix] 기본 라우트가 LAN이 아님: {default_ip}")
    return True


def fix_network_priority_netsh() -> bool:
    """netsh로 네트워크 메트릭 조정 (관리자 권한 필요)

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
            print("[NetworkFix] netsh 사용 불가")
            return False

        success = False

        # 1. Tailscale 메트릭 올리기 (낮은 우선순위)
        subprocess.run(
            ['netsh', 'interface', 'ipv4', 'set', 'interface', 'Tailscale', 'metric=1000'],
            capture_output=True, timeout=5,
            creationflags=0x08000000
        )

        # 2. 일반 LAN 어댑터 메트릭 낮추기 (높은 우선순위)
        for name in ['Ethernet', 'Ethernet 2', 'Ethernet 3', 'Wi-Fi', 'Local Area Connection']:
            r = subprocess.run(
                ['netsh', 'interface', 'ipv4', 'set', 'interface', name, 'metric=5'],
                capture_output=True, timeout=5,
                creationflags=0x08000000
            )
            if r.returncode == 0:
                print(f"[NetworkFix] {name} metric=5 설정 완료")
                success = True

        return success

    except Exception as e:
        print(f"[NetworkFix] netsh 실행 오류: {e}")
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
                    print(f"[NetworkFix/WMI] {desc} ({ip}) metric=5")
                except Exception:
                    pass

            elif ip.startswith('100.') or 'tailscale' in desc.lower():
                # Tailscale - 낮은 우선순위
                try:
                    adapter.SetIPConnectionMetric(1000)
                    print(f"[NetworkFix/WMI] {desc} ({ip}) metric=1000")
                except Exception:
                    pass

            elif ip.startswith('169.254.'):
                # APIPA - 최저 우선순위
                try:
                    adapter.SetIPConnectionMetric(2000)
                    print(f"[NetworkFix/WMI] {desc} ({ip}) metric=2000")
                except Exception:
                    pass

        return True

    except ImportError:
        print("[NetworkFix] WMI 모듈 없음 (pip install wmi)")
        return False
    except Exception as e:
        print(f"[NetworkFix] WMI 오류: {e}")
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
                print("[IPForward] 레지스트리 IP Forwarding 설정 완료")
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
            print("[IPForward] 레지스트리 이미 활성화됨")

        # 런타임 forwarding 활성화 (즉시 적용, 재부팅 불필요)
        _enable_runtime_forwarding()

        return registry_ok

    except Exception as e:
        print(f"[IPForward] 오류: {e}")

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
                print(f"[IPForward] interface {idx} ({name}) forwarding=enabled")

    except Exception as e:
        print(f"[IPForward] 런타임 forwarding 설정 오류: {e}")


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
            ['tailscale', 'up', f'--advertise-routes={subnet}', '--accept-routes'],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000
        )
        if r.returncode == 0:
            print(f"[Tailscale] 서브넷 라우팅 광고: {subnet} (via {tailscale_ip})")
        else:
            print(f"[Tailscale] 서브넷 라우팅 설정 실패: {r.stderr.strip()}")
    except Exception as e:
        print(f"[Tailscale] route 설정 실패 (무시): {e}")


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
                print(f"[Firewall] {name} ({proto} {ports}) 규칙 추가")
        except Exception as e:
            print(f"[Firewall] {name} 규칙 추가 실패: {e}")


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

    print(f"[Tailscale] 관제 PC 서브넷 라우팅 설정: LAN={lan_ip} TS={tailscale_ip}")

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
            ['tailscale', 'up', '--accept-routes'],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000
        )
    except Exception:
        pass


def _ensure_tailscale_high_metric():
    """Tailscale 인터페이스 메트릭을 항상 높게 설정

    Tailscale이 기본 라우트를 빼앗지 않도록 메트릭을 1000으로 설정.
    LAN이 이미 기본 라우트여도 매번 실행 — 재부팅/Tailscale 재시작 시 리셋 방지.
    """
    if not is_admin():
        return

    try:
        # Tailscale 인터페이스 메트릭 올리기 (이미 1000이면 무시됨)
        result = subprocess.run(
            ['netsh', 'interface', 'ipv4', 'set', 'interface', 'Tailscale', 'metric=1000'],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000
        )
        if result.returncode == 0:
            print("[NetworkFix] Tailscale metric=1000 설정 완료 (LAN 우선)")
    except Exception:
        pass


def auto_fix_network():
    """앱 시작 시 자동 네트워크 우선순위 조정

    ★ 핵심: Tailscale이 기본 라우트를 빼앗지 않도록 항상 메트릭 조정
    1. Tailscale 메트릭을 항상 높게 설정 (매번 실행)
    2. 기본 라우트가 LAN이 아닌 경우 추가 수정
    """
    try:
        # ★ Tailscale 메트릭은 항상 높게 유지 (기본 라우트 상관없이)
        _ensure_tailscale_high_metric()

        if not needs_network_fix():
            print("[NetworkFix] 네트워크 정상 (LAN이 기본 라우트)")
            return

        default_ip = get_default_route_ip()
        print(f"[NetworkFix] 기본 라우트 IP: {default_ip} - 수정 필요")

        if not is_admin():
            print("[NetworkFix] 관리자 권한 없음 - 메트릭 변경 건너뜀")
            print("[NetworkFix] WellcomLAND는 내부적으로 올바른 LAN IP를 사용합니다")
            return

        # 방법 1: netsh (가장 일반적)
        if fix_network_priority_netsh():
            new_ip = get_default_route_ip()
            print(f"[NetworkFix] netsh로 수정 완료. 새 기본 라우트: {new_ip}")
            return

        # 방법 2: WMI (cmd/netsh 차단된 경우)
        if fix_network_priority_wmi():
            new_ip = get_default_route_ip()
            print(f"[NetworkFix] WMI로 수정 완료. 새 기본 라우트: {new_ip}")
            return

        print("[NetworkFix] 자동 수정 실패 - WellcomLAND는 내부적으로 올바른 LAN IP를 사용합니다")

    except Exception as e:
        print(f"[NetworkFix] 오류: {e}")
