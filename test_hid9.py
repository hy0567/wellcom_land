"""
1. 먼저 Enter로 줄 구분하여 각 방법별 결과를 확인
2. 한 방법 시도 후 'test' 타이핑
3. 영문 test가 나오면 그 방법이 작동한 것
"""
import paramiko, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.68.61', port=22, username='root', password='luckfox', timeout=5)

def run(cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=5)
    return stdout.read().decode('utf-8', errors='replace').strip()

KEY = {
    'enter': 0x28, 't': 0x17, 'e': 0x08, 's': 0x16, '1': 0x1E,
    '2': 0x1F, '3': 0x20, '4': 0x21, '5': 0x22, '6': 0x23, '7': 0x24,
}

def type_key(keycode, mod=0):
    run(
        "echo -ne '\\x{:02x}\\x00\\x{:02x}\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
        "usleep 60000; "
        "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
        "usleep 40000".format(mod, keycode)
    )

def type_num(n):
    """숫자 입력"""
    type_key(KEY[str(n)])

def type_enter():
    type_key(KEY['enter'])

def release():
    run("echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0")

def type_test():
    """t, e, s, t 입력"""
    for ch in ['t', 'e', 's', 't']:
        type_key(KEY[ch])

# 초기화
release()
time.sleep(0.3)

methods = [
    ("1", "Ctrl+Space",
     "echo -ne '\\x01\\x00\\x2c\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
     "usleep 150000; "
     "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0"),

    ("2", "Right Alt",
     "echo -ne '\\x40\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
     "usleep 150000; "
     "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0"),

    ("3", "LANG1 (0x90)",
     "echo -ne '\\x00\\x00\\x90\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
     "usleep 150000; "
     "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0"),

    ("4", "LANG2 (0x91)",
     "echo -ne '\\x00\\x00\\x91\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
     "usleep 150000; "
     "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0"),

    ("5", "Shift+Space",
     "echo -ne '\\x02\\x00\\x2c\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
     "usleep 150000; "
     "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0"),

    ("6", "Alt+Shift",
     "echo -ne '\\x04\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
     "usleep 50000; "
     "echo -ne '\\x06\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
     "usleep 120000; "
     "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0"),

    ("7", "Win+Space",
     "echo -ne '\\x08\\x00\\x2c\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0; "
     "usleep 200000; "
     "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0"),
]

print("Testing... check remote PC notepad")
print("Each line: [number]test")
print("If 'test' appears as Korean, that method did NOT switch to English")
print("If 'test' appears as English, that method WORKS")
print()

for num, name, cmd in methods:
    # 줄번호 입력
    type_num(int(num))
    time.sleep(0.2)

    # 전환 시도
    run(cmd)
    time.sleep(0.5)

    # test 타이핑
    type_test()
    time.sleep(0.3)

    # Enter로 줄바꿈
    type_enter()
    time.sleep(0.5)

    # 다시 전환 (원래 상태로 복귀) - 같은 키로
    run(cmd)
    time.sleep(0.5)

    print(f"  {num}. {name} - sent")

release()
ssh.close()
print()
print("Check notepad! Each line shows: [number][test or korean]")
print("DONE")
