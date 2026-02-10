"""직접 SSH로 HID Ctrl+Shift 테스트"""
import paramiko, time, struct

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.68.61', port=22, username='root', password='luckfox', timeout=5)
shell = ssh.invoke_shell()
time.sleep(0.5)
if shell.recv_ready(): shell.recv(4096)

# hidg0 상태
shell.send('ls -la /dev/hidg0\n')
time.sleep(0.5)
out = shell.recv(4096).decode('utf-8', errors='replace')
print('=== HIDG0 ===')
print(out)

def make_hex(data):
    parts = []
    for b in data:
        parts.append('\\x{:02x}'.format(b))
    return ''.join(parts)

release = struct.pack('BBBBBBBB', 0, 0, 0, 0, 0, 0, 0, 0)
ctrl = struct.pack('BBBBBBBB', 0x01, 0, 0, 0, 0, 0, 0, 0)
ctrl_shift = struct.pack('BBBBBBBB', 0x03, 0, 0, 0, 0, 0, 0, 0)

steps = [
    ('1_release', release, 0.1),
    ('2_ctrl', ctrl, 0.1),
    ('3_ctrl_shift', ctrl_shift, 0.15),
    ('4_ctrl', ctrl, 0.1),
    ('5_release', release, 0.1),
    ('6_release', release, 0.1),
]

for name, data, delay in steps:
    hex_str = make_hex(data)
    cmd = "echo -ne '{}' > /dev/hidg0 && echo OK_{}\n".format(hex_str, name)
    shell.send(cmd)
    time.sleep(delay)
    out = shell.recv(4096).decode('utf-8', errors='replace')
    ok = 'OK_' in out
    print(f'  {name}: {"SUCCESS" if ok else "FAIL"} ({out.strip()[-30:]})')

ssh.close()
print('\nDONE')
