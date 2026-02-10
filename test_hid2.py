"""SSH로 HID 상태 확인 + Ctrl+Space 직접 테스트"""
import paramiko, time, struct

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.68.61', port=22, username='root', password='luckfox', timeout=5)
shell = ssh.invoke_shell()
time.sleep(0.5)
if shell.recv_ready(): shell.recv(4096)

def make_hex(data):
    parts = []
    for b in data:
        parts.append('\\x{:02x}'.format(b))
    return ''.join(parts)

def send_cmd(shell, cmd, delay=0.3):
    shell.send(cmd + '\n')
    time.sleep(delay)
    out = ''
    if shell.recv_ready():
        out = shell.recv(8192).decode('utf-8', errors='replace')
    return out

# 1. kvm_app PID 및 hidg0 fd 상태
print("=== 1. kvm_app 프로세스 ===")
out = send_cmd(shell, 'ps aux | grep kvm_app | grep -v grep')
print(out)

print("=== 2. /dev/hidg0 상태 ===")
out = send_cmd(shell, 'ls -la /dev/hidg0')
print(out)

# kvm_app의 hidg0 fd 확인 (deleted 여부)
print("=== 3. kvm_app fd → hidg0 (deleted 체크) ===")
out = send_cmd(shell, 'for p in /proc/[0-9]*/fd/*; do ls -la "$p" 2>/dev/null; done | grep hidg0', delay=1.0)
print(out)

# 2. 직접 hidg0에 쓰기 테스트
print("=== 4. 직접 hidg0 쓰기 테스트 ===")
release = struct.pack('BBBBBBBB', 0, 0, 0, 0, 0, 0, 0, 0)
out = send_cmd(shell, "echo -ne '{}' > /dev/hidg0 && echo WRITE_OK || echo WRITE_FAIL".format(make_hex(release)))
print(f"  release: {out.strip()}")

# 3. Ctrl+Space 전송
print("\n=== 5. Ctrl+Space 한/영 전환 테스트 ===")

ctrl = struct.pack('BBBBBBBB', 0x01, 0, 0, 0, 0, 0, 0, 0)
ctrl_space = struct.pack('BBBBBBBB', 0x01, 0, 0x2C, 0, 0, 0, 0, 0)

steps = [
    ('release', release, 0.1),
    ('ctrl_down', ctrl, 0.1),
    ('ctrl+space_down', ctrl_space, 0.15),
    ('ctrl_only', ctrl, 0.1),
    ('release', release, 0.1),
]

for name, data, delay in steps:
    hex_str = make_hex(data)
    raw_bytes = ' '.join('{:02x}'.format(b) for b in data)
    cmd = "echo -ne '{}' > /dev/hidg0 && echo OK || echo FAIL".format(hex_str)
    out = send_cmd(shell, cmd, delay)
    ok = 'OK' in out and 'FAIL' not in out
    print(f"  {name:20s} [{raw_bytes}] → {'OK' if ok else 'FAIL'}")

# 4. 추가: 일반 키 테스트 (a 키)
print("\n=== 6. 일반 키 테스트 (a 키) ===")
a_down = struct.pack('BBBBBBBB', 0, 0, 0x04, 0, 0, 0, 0, 0)
out = send_cmd(shell, "echo -ne '{}' > /dev/hidg0 && echo OK".format(make_hex(a_down)), 0.1)
print(f"  a_down: {'OK' if 'OK' in out else 'FAIL'}")
out = send_cmd(shell, "echo -ne '{}' > /dev/hidg0 && echo OK".format(make_hex(release)), 0.1)
print(f"  release: {'OK' if 'OK' in out else 'FAIL'}")

ssh.close()
print("\nDONE")
