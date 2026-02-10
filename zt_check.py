import subprocess, os, socket

OUT = r"C:\Users\-\PycharmProjects\pythonProject2\ipkvm\ts_result.txt"

results = []

# 1. Tailscale status
try:
    r = subprocess.run(['tailscale', 'status'], capture_output=True, text=True, timeout=10)
    results.append(f"=== Tailscale Status ===\n{r.stdout.strip()}\n{r.stderr.strip()}")
except Exception as e:
    results.append(f"=== Tailscale Status ===\nERROR: {e}")

# 2. Tailscale IP
try:
    r = subprocess.run(['tailscale', 'ip', '-4'], capture_output=True, text=True, timeout=10)
    results.append(f"=== Tailscale IP ===\n{r.stdout.strip()}")
except Exception as e:
    results.append(f"=== Tailscale IP ===\nERROR: {e}")

# 3. Tailscale peers
try:
    r = subprocess.run(['tailscale', 'status', '--peers'], capture_output=True, text=True, timeout=10)
    results.append(f"=== Tailscale Peers ===\n{r.stdout.strip()}")
except Exception as e:
    results.append(f"=== Tailscale Peers ===\nERROR: {e}")

# 4. ipconfig
try:
    r = subprocess.run(['ipconfig'], capture_output=True, text=True, timeout=10)
    lines = [l for l in r.stdout.split('\n') if 'IPv4' in l or 'Tailscale' in l or 'Ethernet' in l.strip()[:10]]
    results.append(f"=== IPv4 Addresses ===\n" + '\n'.join(lines))
except Exception as e:
    results.append(f"=== IPv4 ===\nERROR: {e}")

# 5. Socket IPs
try:
    hostname = socket.gethostname()
    addr_infos = socket.getaddrinfo(hostname, None, socket.AF_INET)
    ips = list(set([info[4][0] for info in addr_infos if info[4][0] != '127.0.0.1']))
    results.append(f"=== Socket IPs ===\n{ips}")
except Exception as e:
    results.append(f"=== Socket IPs ===\nERROR: {e}")

output = '\n\n'.join(results)
with open(OUT, 'w', encoding='utf-8') as f:
    f.write(output)
print(output)
