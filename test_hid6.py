"""실제 키 입력 동작 확인 + 다양한 한/영 전환 키 테스트"""
import paramiko, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.68.61', port=22, username='root', password='luckfox', timeout=5)

def run(cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=5)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    return out, err

# 1. 'a' 키 입력 테스트 (이게 원격 PC 메모장에서 보이는지 확인)
print("=== 1. 'a' 키 입력 (원격 PC에서 확인) ===")
out, err = run(
    "echo -ne '\\x00\\x00\\x04\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 100000; "
    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "echo A_DONE"
)
print(f"  결과: {out} / err: {err}")

time.sleep(1)

# 2. 한/영 전환 방법 1: Ctrl+Space
print("\n=== 2. Ctrl+Space ===")
out, err = run(
    "echo -ne '\\x01\\x00\\x2c\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 150000; "
    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "echo CTRL_SPACE_DONE"
)
print(f"  결과: {out} / err: {err}")

time.sleep(1)

# 3. 'a' 다시 입력 (한글 ㅁ 이 나오면 성공)
print("\n=== 3. 'a' 키 다시 입력 (ㅁ이면 한/영 전환 성공) ===")
out, err = run(
    "echo -ne '\\x00\\x00\\x04\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 100000; "
    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "echo A2_DONE"
)
print(f"  결과: {out} / err: {err}")

time.sleep(1)

# 4. 한/영 전환 방법 2: Right Alt (0x40)
print("\n=== 4. Right Alt ===")
out, err = run(
    "echo -ne '\\x40\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 150000; "
    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "echo RALT_DONE"
)
print(f"  결과: {out} / err: {err}")

time.sleep(1)

# 5. 'a' 다시 입력
print("\n=== 5. 'a' 키 입력 (Right Alt 후) ===")
out, err = run(
    "echo -ne '\\x00\\x00\\x04\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 100000; "
    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "echo A3_DONE"
)
print(f"  결과: {out} / err: {err}")

time.sleep(1)

# 6. 한/영 전환 방법 3: HID LANG1 (0x90) - Korean specific
print("\n=== 6. HID LANG1 (0x90) ===")
out, err = run(
    "echo -ne '\\x00\\x00\\x90\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 150000; "
    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "echo LANG1_DONE"
)
print(f"  결과: {out} / err: {err}")

time.sleep(1)

# 7. 한/영 전환 방법 4: HID Hangul key (0x90은 keypad, 실제 한영키=0x90이 아님)
# Windows에서 한/영 키는 실제로 HID usage 0x90 (Keyboard LANG1)
# 또는 F13(0x68)을 매핑하기도 함
# 한/영 전환 방법 5: Ctrl+Shift (이전에 이걸로 됐었음)
print("\n=== 7. Ctrl+Shift (이전에 작동했던 방식) ===")
out, err = run(
    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 50000; "
    "echo -ne '\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 50000; "
    "echo -ne '\\x03\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 100000; "
    "echo -ne '\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 50000; "
    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "echo CTRL_SHIFT_DONE"
)
print(f"  결과: {out} / err: {err}")

time.sleep(1)

# 8. 'a' 다시
print("\n=== 8. 'a' 키 입력 (Ctrl+Shift 후) ===")
out, err = run(
    "echo -ne '\\x00\\x00\\x04\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "usleep 100000; "
    "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
    "echo A4_DONE"
)
print(f"  결과: {out} / err: {err}")

# 최종 릴리즈
run("echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0")

ssh.close()
print("\n원격 PC 화면에서 a → (전환) → a 입력을 확인해주세요!")
print("어떤 방식에서 한글(ㅁ)이 입력되는지 알려주세요.")
print("DONE")
