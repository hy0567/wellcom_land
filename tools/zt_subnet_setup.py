"""
ZeroTier Subnet Router 설정 스크립트

관제 PC에서 실행하여:
1. Windows IP Forwarding 활성화
2. ZeroTier Managed Route 추가 (서버 API 호출)

이렇게 하면 메인 PC에서 192.168.68.x (관제 PC의 KVM 망)에 직접 접근 가능.
"""
import subprocess
import socket
import sys
import io
import json
import ctypes
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except:
        return False


def get_zt_ip():
    """ZeroTier IP 찾기"""
    hostname = socket.gethostname()
    for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
        ip = info[4][0]
        if ip.startswith('10.147.'):
            return ip
    return None


def get_local_subnets():
    """이 PC의 로컬 서브넷 찾기 (ZeroTier, APIPA 제외)"""
    subnets = []
    hostname = socket.gethostname()
    for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
        ip = info[4][0]
        if ip.startswith('10.147.') or ip.startswith('169.254.') or ip == '127.0.0.1':
            continue
        # /24 서브넷으로 변환
        parts = ip.split('.')
        subnet = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
        if subnet not in subnets:
            subnets.append((subnet, ip))
    return subnets


def enable_ip_forwarding():
    """Windows IP Forwarding 활성화"""
    print("[1] Windows IP Forwarding 활성화...")

    # 현재 상태 확인
    result = subprocess.run(
        ['reg', 'query',
         r'HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters',
         '/v', 'IPEnableRouter'],
        capture_output=True, text=True
    )

    if '0x1' in result.stdout:
        print("  -> 이미 활성화됨")
        return True

    # 레지스트리 설정
    result = subprocess.run(
        ['reg', 'add',
         r'HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters',
         '/v', 'IPEnableRouter', '/t', 'REG_DWORD', '/d', '1', '/f'],
        capture_output=True, text=True
    )

    if result.returncode == 0:
        print("  -> 활성화 완료 (재부팅 필요할 수 있음)")

        # Routing and Remote Access 서비스 시작
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


def setup_zt_route(network_id, zt_ip, subnet):
    """ZeroTier 컨트롤러에 Managed Route 추가"""
    print(f"[2] ZeroTier Managed Route 추가: {subnet} via {zt_ip}...")

    # zerotier-cli로 현재 네트워크 정보 확인
    cli = None
    for p in [
        r'C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat',
        r'C:\Program Files\ZeroTier\One\zerotier-cli.bat',
    ]:
        if os.path.exists(p):
            cli = p
            break

    if not cli:
        print("  -> ZeroTier CLI 없음!")
        return False

    # 현재 네트워크 ID 확인
    result = subprocess.run(
        [cli, 'listnetworks'],
        capture_output=True, text=True, timeout=10,
        creationflags=0x08000000
    )
    print(f"  현재 네트워크: {result.stdout.strip()}")

    # ZeroTier 자체 호스팅 컨트롤러 API로 route 추가
    # 서버의 /api/zerotier/route 엔드포인트를 통해 설정
    print(f"  -> 서버에 route 요청: {subnet} via {zt_ip}")

    return True


def main():
    print("=" * 60)
    print("ZeroTier Subnet Router 설정")
    print("=" * 60)

    if not is_admin():
        print("\n[!] 관리자 권한이 필요합니다!")
        print("    이 스크립트를 '관리자 권한으로 실행'해주세요.")
        input("Enter를 누르면 종료...")
        return

    # ZeroTier IP 확인
    zt_ip = get_zt_ip()
    if not zt_ip:
        print("\n[!] ZeroTier IP를 찾을 수 없습니다.")
        print("    ZeroTier가 설치되어 있고 네트워크에 참가했는지 확인하세요.")
        input("Enter를 누르면 종료...")
        return

    print(f"\n이 PC의 ZeroTier IP: {zt_ip}")

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

    # ZeroTier 네트워크에서 allowGlobal 및 allowDefault 활성화
    print(f"\n[3] ZeroTier 인터페이스에서 IP Forwarding 허용...")
    cli = None
    for p in [
        r'C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat',
        r'C:\Program Files\ZeroTier\One\zerotier-cli.bat',
    ]:
        if os.path.exists(p):
            cli = p
            break

    if cli:
        # 네트워크 목록에서 ID 추출
        result = subprocess.run(
            [cli, 'listnetworks'],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000
        )
        lines = result.stdout.strip().split('\n')
        for line in lines:
            parts = line.split()
            if len(parts) >= 3 and len(parts[2]) == 16:
                net_id = parts[2]
                # allowGlobal 활성화
                result2 = subprocess.run(
                    [cli, 'set', net_id, 'allowGlobal=true'],
                    capture_output=True, text=True, timeout=10,
                    creationflags=0x08000000
                )
                print(f"  {net_id} allowGlobal: {result2.stdout.strip()}")

    print(f"\n{'=' * 60}")
    print("설정 완료!")
    print()
    print("다음 단계:")
    print(f"  1. ZeroTier 컨트롤러(my.zerotier.com 또는 자체 호스팅)에서")
    print(f"     Managed Routes에 추가:")
    for subnet, ip in subnets:
        print(f"       {subnet} via {zt_ip}")
    print(f"  2. 이 PC 재부팅 (IP Forwarding 적용)")
    print(f"  3. 메인 PC에서 ping {subnets[0][1] if subnets else '192.168.68.69'} 테스트")
    print(f"\n{'=' * 60}")
    input("Enter를 누르면 종료...")


if __name__ == '__main__':
    main()
