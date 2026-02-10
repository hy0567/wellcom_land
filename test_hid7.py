"""Ctrl+Space 토글 2회 테스트 (한→영→한 확인)"""
import paramiko, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.68.61', port=22, username='root', password='luckfox', timeout=5)

def run(cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=5)
    return stdout.read().decode('utf-8', errors='replace').strip()

def send_ctrl_space():
    """Ctrl+Space 한/영 전환 — 완전한 순차 릴리즈"""
    return run(
        # 1. 완전 릴리즈 (이전 상태 클리어)
        "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
        "usleep 80000; "
        # 2. Ctrl만 누름
        "echo -ne '\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
        "usleep 80000; "
        # 3. Ctrl + Space 누름
        "echo -ne '\\x01\\x00\\x2c\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
        "usleep 120000; "
        # 4. Space만 놓음 (Ctrl 유지)
        "echo -ne '\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
        "usleep 80000; "
        # 5. 전체 릴리즈
        "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
        "usleep 80000; "
        # 6. 안전 릴리즈
        "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
        "echo TOGGLE_OK"
    )

def send_a():
    """a키 입력"""
    return run(
        "echo -ne '\\x00\\x00\\x04\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
        "usleep 100000; "
        "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
        "echo A_OK"
    )

# 시작: 완전 릴리즈
run("echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0")
time.sleep(0.5)

print("=== 테스트 시작 (원격 PC 메모장에서 확인) ===")
print()

# 1. a 입력 (현재 상태 확인)
print("1. 'a' 입력 (현재 영문이면 a, 한글이면 ㅁ)")
send_a()
time.sleep(1)

# 2. 첫 번째 Ctrl+Space (영→한 전환)
print("2. Ctrl+Space 전환 #1")
result = send_ctrl_space()
print(f"   결과: {result}")
time.sleep(1)

# 3. a 입력 (한글이면 ㅁ)
print("3. 'a' 입력 (전환 후)")
send_a()
time.sleep(1)

# 4. 두 번째 Ctrl+Space (한→영 전환)
print("4. Ctrl+Space 전환 #2")
result = send_ctrl_space()
print(f"   결과: {result}")
time.sleep(1)

# 5. a 입력 (다시 영문이면 a)
print("5. 'a' 입력 (두번째 전환 후)")
send_a()
time.sleep(1)

# 6. 세 번째 전환 + a
print("6. Ctrl+Space 전환 #3")
result = send_ctrl_space()
print(f"   결과: {result}")
time.sleep(1)

print("7. 'a' 입력 (세번째 전환 후)")
send_a()

# 최종 릴리즈
run("echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0")

ssh.close()
print()
print("=== 원격 PC에서 확인 ===")
print("예상 결과: a ㅁ a ㅁ (번갈아 전환)")
print("DONE")
