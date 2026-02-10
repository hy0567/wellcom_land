"""hidg0 쓰기 + 실제 한/영 전환 테스트 (출력 정확히 확인)"""
import paramiko, time, struct

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.68.61', port=22, username='root', password='luckfox', timeout=5)

# exec_command 방식으로 정확한 결과 확인
def run(cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=5)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()
    return out, err

# 1. 기본 쓰기 테스트
print("=== 1. echo 쓰기 테스트 ===")
out, err = run("echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0 2>&1; echo $?")
print(f"  out: [{out}]  err: [{err}]")

# 2. Ctrl+Space 전송 (순차적으로)
print("\n=== 2. Ctrl+Space 전송 ===")

cmds = [
    ("release",     "\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00"),
    ("ctrl",        "\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x00"),
    ("ctrl+space",  "\\x01\\x00\\x2c\\x00\\x00\\x00\\x00\\x00"),
    ("ctrl",        "\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x00"),
    ("release",     "\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00"),
]

# 하나의 셸 스크립트로 만들어서 실행
script_lines = ["#!/bin/sh"]
for name, hexdata in cmds:
    script_lines.append(f"echo -ne '{hexdata}' > /dev/hidg0 && echo '{name}: OK' || echo '{name}: FAIL'")
    if name == "ctrl+space":
        script_lines.append("usleep 150000 2>/dev/null || sleep 0.15")
    else:
        script_lines.append("usleep 80000 2>/dev/null || sleep 0.08")

script = '\n'.join(script_lines)
out, err = run(script)
print(f"  결과:\n{out}")
if err:
    print(f"  에러: {err}")

# 3. a 키 테스트
print("\n=== 3. a 키 타이핑 테스트 ===")
out, err = run("echo -ne '\\x00\\x00\\x04\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0 && echo 'a_down: OK'; usleep 100000; echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0 && echo 'release: OK'")
print(f"  결과: {out}")

ssh.close()
print("\nDONE")
