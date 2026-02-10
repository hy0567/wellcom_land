"""현재 한글 상태에서 영문으로 전환 — 다양한 방법 시도"""
import paramiko, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.68.61', port=22, username='root', password='luckfox', timeout=5)

def run(cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=5)
    return stdout.read().decode('utf-8', errors='replace').strip()

def send_a():
    run(
        "echo -ne '\\x00\\x00\\x04\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
        "usleep 100000; "
        "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0"
    )

def release():
    run("echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0")

# 완전 릴리즈
release()
time.sleep(0.5)

print("=== hangul->english test ===")
print("(after each method, 'a' key pressed)")
print()

# 방법 1: 한/영 키 = HID 0x90 (LANG1)
print("방법1: LANG1 (0x90)")
run(
    "echo -ne '\\x00\\x00\\x90\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 150000; "
    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0"
)
time.sleep(0.5)
send_a()
time.sleep(1.5)

# 방법 2: LANG2 (0x91) — 영문 전환 전용
print("방법2: LANG2 (0x91)")
run(
    "echo -ne '\\x00\\x00\\x91\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 150000; "
    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0"
)
time.sleep(0.5)
send_a()
time.sleep(1.5)

# 방법 3: Right Alt (한/영 키 = Right Alt 매핑)
print("방법3: Right Alt (0x40)")
run(
    "echo -ne '\\x40\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 150000; "
    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0"
)
time.sleep(0.5)
send_a()
time.sleep(1.5)

# 방법 4: Shift+Space
print("방법4: Shift+Space")
run(
    "echo -ne '\\x02\\x00\\x2c\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 150000; "
    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0"
)
time.sleep(0.5)
send_a()
time.sleep(1.5)

# 방법 5: Alt+Shift (Windows 입력 언어 전환)
print("방법5: Alt+Shift (Windows 언어 전환)")
run(
    "echo -ne '\\x04\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 50000; "
    "echo -ne '\\x06\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 120000; "
    "echo -ne '\\x04\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 50000; "
    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0"
)
time.sleep(0.5)
send_a()
time.sleep(1.5)

# 방법 6: Win+Space (Windows 10/11 입력 방법 전환)
print("방법6: Win+Space")
run(
    "echo -ne '\\x08\\x00\\x2c\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 200000; "
    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0"
)
time.sleep(1)
send_a()
time.sleep(1.5)

# 방법 7: Ctrl+Space 다시 한번
print("방법7: Ctrl+Space (재시도)")
run(
    "echo -ne '\\x01\\x00\\x2c\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 150000; "
    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0"
)
time.sleep(0.5)
send_a()

release()
ssh.close()
print()
print("=== 원격 PC 메모장에서 확인 ===")
print("순서: 방법1 a, 방법2 a, 방법3 a, 방법4 a, 방법5 a, 방법6 a, 방법7 a")
print("어느 시점에서 'a'(영문)가 나왔는지 알려주세요!")
print("DONE")
