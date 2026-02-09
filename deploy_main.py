import paramiko
import time
import os
import sys

# === Configuration ===
SSH_HOST = "log.wellcomll.org"
SSH_PORT = 479
SSH_USER = "root"
SSH_PASS = "ghdydhy86@"
SOURCE_FILE = r"C:\Users\-\PycharmProjects\pythonProject2\ipkvm\server\main.py"
TARGET_PATH = "/opt/wellcomland-api/main.py"
SERVICE_NAME = "wellcomland-api"

def run_ssh_command(ssh, command, description=""):
    """Execute a command over SSH and return stdout/stderr."""
    if description:
        print(f"\n{'='*60}")
        print(f"  {description}")
        print(f"{'='*60}")
    print(f"  CMD: {command}")
    stdin, stdout, stderr = ssh.exec_command(command, timeout=30)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    if out:
        print(f"  STDOUT:\n{out}")
    if err:
        print(f"  STDERR:\n{err}")
    print(f"  EXIT CODE: {exit_code}")
    return exit_code, out, err

def main():
    # Validate source file
    if not os.path.isfile(SOURCE_FILE):
        print(f"[ERROR] Source file not found: {SOURCE_FILE}")
        sys.exit(1)
    file_size = os.path.getsize(SOURCE_FILE)
    print(f"[INFO] Source file: {SOURCE_FILE} ({file_size:,} bytes)")

    # Create SSH client
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        # --- Step 0: Connect ---
        print(f"\n[CONNECT] Connecting to {SSH_HOST}:{SSH_PORT} as {SSH_USER} ...")
        ssh.connect(
            hostname=SSH_HOST,
            port=SSH_PORT,
            username=SSH_USER,
            password=SSH_PASS,
            timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )
        print("[CONNECT] SSH connection established.")

        # --- Step 1: Upload main.py via SFTP ---
        print(f"\n{'='*60}")
        print(f"  STEP 1: Upload main.py -> {TARGET_PATH}")
        print(f"{'='*60}")
        sftp = ssh.open_sftp()

        # Backup existing file first
        run_ssh_command(ssh,
            f"cp {TARGET_PATH} {TARGET_PATH}.bak.$(date +%Y%m%d_%H%M%S) 2>/dev/null; echo 'backup done'",
            "Backup existing file")

        sftp.put(SOURCE_FILE, TARGET_PATH)
        remote_stat = sftp.stat(TARGET_PATH)
        print(f"  [OK] Uploaded successfully. Remote size: {remote_stat.st_size:,} bytes")

        if remote_stat.st_size != file_size:
            print(f"  [WARN] Size mismatch! Local={file_size}, Remote={remote_stat.st_size}")
        else:
            print(f"  [OK] Size verified: {file_size:,} bytes match.")
        sftp.close()

        # --- Step 2: Restart the service ---
        run_ssh_command(ssh,
            f"systemctl restart {SERVICE_NAME}",
            f"STEP 2: Restart {SERVICE_NAME} service")

        # --- Step 3: Wait 3 seconds, then check status ---
        print(f"\n[WAIT] Sleeping 3 seconds for service startup ...")
        time.sleep(3)

        exit_code, out, err = run_ssh_command(ssh,
            f"systemctl status {SERVICE_NAME} --no-pager -l",
            f"STEP 3: Check {SERVICE_NAME} service status")

        if exit_code == 0:
            print("  [OK] Service is running.")
        else:
            print("  [WARN] Service may not be running properly.")

        # --- Step 4: Verify API version ---
        run_ssh_command(ssh,
            "curl -s --max-time 5 http://127.0.0.1:8000/api/version",
            "STEP 4: Verify API version via curl")

        print(f"\n{'='*60}")
        print("  DEPLOYMENT COMPLETE")
        print(f"{'='*60}")

    except paramiko.AuthenticationException:
        print("[ERROR] SSH authentication failed. Check username/password.")
        sys.exit(1)
    except paramiko.SSHException as e:
        print(f"[ERROR] SSH error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        sys.exit(1)
    finally:
        ssh.close()
        print("[INFO] SSH connection closed.")

if __name__ == "__main__":
    main()
