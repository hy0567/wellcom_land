"""Right Alt (0x40) 한/영 전환 4회 토글 테스트"""
import paramiko, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.68.61', port=22, username='root', password='luckfox', timeout=5)

def run(cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=5)
    return stdout.read().decode('utf-8', errors='replace').strip()

KEY = {'enter': 0x28, 't': 0x17, 'e': 0x08, 's': 0x16}

def type_key(keycode):
    run(
        "echo -ne '\\x00\\x00\\x{:02x}\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
        "usleep 60000; "
        "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
        "usleep 40000".format(keycode)
    )

def type_test():
    for ch in ['t', 'e', 's', 't']:
        type_key(KEY[ch])

def type_enter():
    type_key(KEY['enter'])

def right_alt_toggle():
    run(
        "echo -ne '\\x40\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
        "usleep 150000; "
        "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0"
    )

def release():
    run("echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0")

release()
time.sleep(0.3)

for i in range(1, 5):
    right_alt_toggle()
    time.sleep(0.5)
    type_test()
    time.sleep(0.2)
    type_enter()
    time.sleep(0.5)
    print(f"  toggle #{i} done")

release()
ssh.close()
print("Expected: test / korean / test / korean (alternating)")
print("DONE")
