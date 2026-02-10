"""hidg0 쓰기 실패 원인 확인"""
import paramiko, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.68.61', port=22, username='root', password='luckfox', timeout=5)
shell = ssh.invoke_shell()
time.sleep(0.5)
if shell.recv_ready(): shell.recv(4096)

def send_cmd(shell, cmd, delay=0.5):
    shell.send(cmd + '\n')
    time.sleep(delay)
    out = ''
    while shell.recv_ready():
        out += shell.recv(8192).decode('utf-8', errors='replace')
    return out

# 1. 장치 상태
print("=== /dev/hidg* 권한 ===")
out = send_cmd(shell, 'ls -la /dev/hidg*')
print(out)

# 2. 직접 쓰기 + stderr 확인
print("=== echo 쓰기 에러 확인 ===")
out = send_cmd(shell, "echo -ne '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' > /dev/hidg0 2>&1; echo EXIT_CODE=$?")
print(out)

# 3. python으로 직접 쓰기 시도
print("=== python 직접 쓰기 ===")
out = send_cmd(shell, "python3 -c \"f=open('/dev/hidg0','wb'); f.write(b'\\x00'*8); f.close(); print('PY_OK')\" 2>&1 || echo PY_FAIL", 1.0)
print(out)

# python 없으면 dd로 시도
print("=== dd 쓰기 ===")
out = send_cmd(shell, "printf '\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00' | dd of=/dev/hidg0 bs=8 count=1 2>&1; echo DD_EXIT=$?")
print(out)

# 4. USB gadget 상태
print("=== USB gadget 상태 ===")
out = send_cmd(shell, 'cat /sys/kernel/config/usb_gadget/kvm/UDC 2>/dev/null')
print(out)

# 5. kvm_app이 hidg0를 독점하고 있는지 확인
print("=== fuser /dev/hidg0 ===")
out = send_cmd(shell, 'fuser /dev/hidg0 2>&1 || echo NO_FUSER')
print(out)

# 6. lsof
print("=== lsof hidg0 ===")
out = send_cmd(shell, 'lsof /dev/hidg0 2>&1 || echo NO_LSOF')
print(out)

ssh.close()
print("DONE")
