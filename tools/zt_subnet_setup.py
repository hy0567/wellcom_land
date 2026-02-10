"""
Tailscale Subnet Router 설정 스크립트

관제 PC에서 실행하여:
1. Windows IP Forwarding 활성화
2. Tailscale 서브넷 라우팅 광고 (tailscale up --advertise-routes)

이렇게 하면 메인 PC에서 192.168.68.x (관제 PC의 KVM 망)에 직접 접근 가능.
"""
import subprocess
import socket
import sys
import io
import ctypes

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except:
        return False


def get_tailscale_ip():
    """Tailscale IP 찾기 (100.x.x.x)"""
    try:
        r = subprocess.run(
            ['tailscale', 'ip', '-4'],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000
        )
        if r.returncode == 0:
            ip = r.stdout.strip().split('\n')[0].strip()
            if ip.startswith('100.'):
                return ip
    except Exception:
        pass

    # 폴백: hostname에서 찾기
    hostname = socket.gethostname()
    for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
        ip = info[4][0]
        if ip.startswith('100.'):
            return ip
    return None


def get_local_subnets():
    """이 PC의 로컬 서브넷 찾기 (Tailscale, APIPA 제외)"""
    subnets = []
    hostname = socket.gethostname()
    for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
        ip = info[4][0]
        if ip.startswith('100.') or ip.startswith('169.254.') or ip == '127.0.0.1':
            continue
        parts = ip.split('.')
        subnet = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
        if subnet not in [s[0] for s in subnets]:
            subnets.append((subnet, ip))
    return subnets


def enable_ip_forwarding():
    """Windows IP Forwarding 활성화"""
    print("[1] Windows IP Forwarding 활성화...")

    result = subprocess.run(
        ['reg', 'query',
         r'HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters',
         '/v', 'IPEnableRouter'],
        capture_output=True, text=True
    )

    if '0x1' in result.stdout:
        print("  -> 이미 활성화됨")
        return True

    result = subprocess.run(
        ['reg', 'add',
         r'HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters',
         '/v', 'IPEnableRouter', '/t', 'REG_DWORD', '/d', '1', '/f'],
        capture_output=True, text=True
    )

    if result.returncode == 0:
        print("  -> 활성화 완료 (재부팅 필요할 수 있음)")
        subprocess.run(
            ['sc', 'config', 'RemoteAccess', 'start=', 'auto'],
            capture_output=True, text=True
        )
        subprocess.run(
            ['net', 'start', 'RemoteAccess'],
            capture_output=True, text=True
        )
        return True
    else:
        print(f"  -> 실패: {result.stderr}")
        return False


def setup_tailscale_routes(subnets):
    """Tailscale 서브넷 라우팅 광고"""
    routes = ','.join(s[0] for s in subnets)
    print(f"[2] Tailscale 서브넷 라우팅 광고: {routes}")

    r = subprocess.run(
        ['tailscale', 'up', f'--advertise-routes={routes}', '--accept-routes'],
        capture_output=True, text=True, timeout=15,
        creationflags=0x08000000
    )

    if r.returncode == 0:
        print("  -> 서브넷 라우팅 광고 완료")
        print("  -> Tailscale 관리 콘솔에서 라우팅 승인 필요:")
        print("     https://login.tailscale.com/admin/machines")
        return True
    else:
        print(f"  -> 실패: {r.stderr.strip()}")
        return False


def main():
    print("=" * 60)
    print("Tailscale Subnet Router 설정")
    print("=" * 60)

    if not is_admin():
        print("\n[!] 관리자 권한이 필요합니다!")
        print("    이 스크립트를 '관리자 권한으로 실행'해주세요.")
        input("Enter를 누르면 종료...")
        return

    # Tailscale IP 확인
    ts_ip = get_tailscale_ip()
    if not ts_ip:
        print("\n[!] Tailscale IP를 찾을 수 없습니다.")
        print("    Tailscale이 설치되어 있고 로그인했는지 확인하세요.")
        input("Enter를 누르면 종료...")
        return

    print(f"\n이 PC의 Tailscale IP: {ts_ip}")

    # 로컬 서브넷 확인
    subnets = get_local_subnets()
    if not subnets:
        print("\n[!] 로컬 서브넷을 찾을 수 없습니다.")
        return

    print(f"\n로컬 서브넷:")
    for subnet, ip in subnets:
        print(f"  {subnet} (via {ip})")

    # IP Forwarding 활성화
    print()
    enable_ip_forwarding()

    # Tailscale 서브넷 라우팅
    print()
    setup_tailscale_routes(subnets)

    print(f"\n{'=' * 60}")
    print("설정 완료!")
    print()
    print("다음 단계:")
    print("  1. Tailscale 관리 콘솔에서 이 머신의 서브넷 라우팅 승인")
    print("     https://login.tailscale.com/admin/machines")
    print("  2. 메인 PC에서 Tailscale이 설치되고 로그인되어 있는지 확인")
    print(f"  3. 메인 PC에서 ping {subnets[0][1] if subnets else '192.168.68.69'} 테스트")
    print(f"\n{'=' * 60}")
    input("Enter를 누르면 종료...")


if __name__ == '__main__':
    main()
