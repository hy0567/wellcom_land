"""
WellcomLAND Network Priority Fix (Python version)
cmd/PowerShell/netsh가 차단된 경량 Windows에서 사용

사용법:
  python fix_network_priority.py

동작:
  1. subprocess netsh 시도
  2. 실패 시 WMI (순수 Python COM) 시도
  3. 실패 시 ctypes Win32 API 시도
"""

import sys
import os
import socket
import subprocess
import ctypes


def is_admin():
    """관리자 권한 확인"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def elevate():
    """관리자 권한으로 재실행"""
    if is_admin():
        return True
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable,
            f'"{os.path.abspath(__file__)}"', None, 1
        )
        sys.exit(0)
    except Exception as e:
        print(f"[ERROR] Admin elevation failed: {e}")
        return False


def get_current_ip():
    """현재 기본 라우트 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


def method_netsh():
    """방법 1: netsh"""
    print("\n[Method 1: netsh]")
    try:
        r = subprocess.run(
            ['netsh', 'interface', 'ipv4', 'show', 'interfaces'],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000
        )
        if r.returncode != 0:
            print("  netsh blocked")
            return False

        print("  [Before]")
        print(r.stdout)

        # Tailscale -> metric 1000
        subprocess.run(
            ['netsh', 'interface', 'ipv4', 'set', 'interface', 'Tailscale', 'metric=1000'],
            capture_output=True, timeout=5, creationflags=0x08000000
        )
        print("  Tailscale metric=1000")

        # LAN adapters -> metric 5
        for name in ['Ethernet', 'Ethernet 2', 'Ethernet 3', 'Wi-Fi',
                      'Local Area Connection', 'LAN']:
            result = subprocess.run(
                ['netsh', 'interface', 'ipv4', 'set', 'interface', name, 'metric=5'],
                capture_output=True, timeout=5, creationflags=0x08000000
            )
            if result.returncode == 0:
                print(f"  {name} metric=5 [OK]")

        print("\n  [After]")
        r2 = subprocess.run(
            ['netsh', 'interface', 'ipv4', 'show', 'interfaces'],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000
        )
        print(r2.stdout)
        return True

    except Exception as e:
        print(f"  netsh error: {e}")
        return False


def method_wmi():
    """방법 2: WMI (Python COM)"""
    print("\n[Method 2: WMI]")
    try:
        import wmi
        c = wmi.WMI()
        adapters = c.Win32_NetworkAdapterConfiguration(IPEnabled=True)

        for adapter in adapters:
            if not adapter.IPAddress:
                continue

            ip = adapter.IPAddress[0]
            desc = adapter.Description or "unknown"

            if ip.startswith('192.168.') or ip.startswith('10.'):
                try:
                    result = adapter.SetIPConnectionMetric(5)
                    status = "OK" if result[0] == 0 else f"code={result[0]}"
                    print(f"  LAN: {desc} ({ip}) metric=5 [{status}]")
                except Exception as e:
                    print(f"  LAN: {desc} ({ip}) FAILED: {e}")

            elif ip.startswith('100.') or 'tailscale' in desc.lower():
                try:
                    result = adapter.SetIPConnectionMetric(1000)
                    status = "OK" if result[0] == 0 else f"code={result[0]}"
                    print(f"  Tailscale: {desc} ({ip}) metric=1000 [{status}]")
                except Exception as e:
                    print(f"  Tailscale: {desc} ({ip}) FAILED: {e}")

            elif ip.startswith('169.254.'):
                try:
                    result = adapter.SetIPConnectionMetric(2000)
                    status = "OK" if result[0] == 0 else f"code={result[0]}"
                    print(f"  APIPA: {desc} ({ip}) metric=2000 [{status}]")
                except Exception as e:
                    print(f"  APIPA: {desc} ({ip}) FAILED: {e}")

        return True

    except ImportError:
        print("  WMI module not found (pip install wmi)")
        return False
    except Exception as e:
        print(f"  WMI error: {e}")
        return False


def method_comtypes_wmi():
    """방법 3: 순수 COM (ctypes로 WMI 직접 호출 - wmi 패키지 불필요)"""
    print("\n[Method 3: COM/WbemScripting]")
    try:
        import win32com.client
        wmi_service = win32com.client.GetObject("winmgmts:")
        adapters = wmi_service.ExecQuery(
            "SELECT * FROM Win32_NetworkAdapterConfiguration WHERE IPEnabled=TRUE"
        )

        for adapter in adapters:
            if not adapter.IPAddress:
                continue

            ip = adapter.IPAddress[0]
            desc = adapter.Description or "unknown"

            if ip.startswith('192.168.') or ip.startswith('10.'):
                try:
                    adapter.SetIPConnectionMetric(5)
                    print(f"  LAN: {desc} ({ip}) metric=5 [OK]")
                except Exception as e:
                    print(f"  LAN: {desc} ({ip}) FAILED: {e}")

            elif ip.startswith('100.') or 'tailscale' in desc.lower():
                try:
                    adapter.SetIPConnectionMetric(1000)
                    print(f"  Tailscale: {desc} ({ip}) metric=1000 [OK]")
                except Exception as e:
                    print(f"  Tailscale: {desc} ({ip}) FAILED: {e}")

            elif ip.startswith('169.254.'):
                try:
                    adapter.SetIPConnectionMetric(2000)
                    print(f"  APIPA: {desc} ({ip}) metric=2000 [OK]")
                except Exception as e:
                    print(f"  APIPA: {desc} ({ip}) FAILED: {e}")

        return True

    except ImportError:
        print("  win32com not available")
        return False
    except Exception as e:
        print(f"  COM error: {e}")
        return False


def main():
    print("=" * 50)
    print("  WellcomLAND Network Priority Fix (Python)")
    print("=" * 50)

    # 관리자 권한 확인/승격
    if not is_admin():
        print("\nRequesting admin rights...")
        elevate()
        return

    print(f"\nCurrent default route: {get_current_ip()}")
    print(f"Admin: {is_admin()}")

    # 순서대로 시도
    success = False

    if not success:
        success = method_netsh()

    if not success:
        success = method_wmi()

    if not success:
        success = method_comtypes_wmi()

    if success:
        print(f"\n[RESULT] New default route: {get_current_ip()}")
        print("\n=== Done! LAN is now primary ===")
    else:
        print("\n[RESULT] All methods failed.")
        print("WellcomLAND handles IP priority internally,")
        print("so the app will still work correctly.")

    print()
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
