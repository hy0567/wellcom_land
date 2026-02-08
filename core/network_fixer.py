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


def auto_fix_network():
    """앱 시작 시 자동 네트워크 우선순위 조정

    1. 기본 라우트가 LAN이 아닌 경우만 실행
    2. 관리자 권한이 있으면 자동 수정 시도
    3. 관리자 권한이 없으면 로그만 남김 (앱 자체는 get_local_ip()로 올바른 IP 사용)
    """
    try:
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
