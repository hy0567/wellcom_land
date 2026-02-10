"""kvm_app 재시작 후 Ctrl+Space 테스트"""
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

# 1. kvm_app 재시작
print("=== kvm_app 재시작 ===")
out = send_cmd(shell, 'killall kvm_app 2>/dev/null; echo KILLED', 2.0)
print(f"  Kill: {'KILLED' if 'KILLED' in out else 'FAIL'}")

out = send_cmd(shell, '/userdata/picokvm/bin/kvm_app > /tmp/kvm_app.log 2>&1 &', 0.5)
out = send_cmd(shell, 'sleep 3 && echo STARTED', 4.0)
print(f"  Start: {'STARTED' if 'STARTED' in out else 'FAIL'}")

# 2. fd 확인
print("\n=== hidg0 fd 확인 ===")
out = send_cmd(shell, 'for p in /proc/[0-9]*/fd/*; do ls -la "$p" 2>/dev/null; done | grep hidg0', 1.0)
has_deleted = '(deleted)' in out
print(f"  deleted: {has_deleted}")
print(f"  {out.strip()[-80:]}")

# 3. Ctrl+Space 테스트
print("\n=== Ctrl+Space 테스트 ===")
release = struct.pack('BBBBBBBB', 0, 0, 0, 0, 0, 0, 0, 0)
ctrl = struct.pack('BBBBBBBB', 0x01, 0, 0, 0, 0, 0, 0, 0)
ctrl_space = struct.pack('BBBBBBBB', 0x01, 0, 0x2C, 0, 0, 0, 0, 0)

steps = [
    ('release', release, 0.1),
    ('ctrl_down', ctrl, 0.1),
    ('ctrl+space', ctrl_space, 0.15),
    ('ctrl_only', ctrl, 0.1),
    ('release', release, 0.1),
]

for name, data, delay in steps:
    hex_str = make_hex(data)
    cmd = "echo -ne '{}' > /dev/hidg0 && echo OK || echo FAIL".format(hex_str)
    out = send_cmd(shell, cmd, delay)
    ok = 'OK' in out and 'FAIL' not in out
    print(f"  {name:20s} -> {'OK' if ok else 'FAIL'}")

ssh.close()
print("\nDONE")
