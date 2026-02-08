import subprocess, os

OUT = r"C:\Users\-\PycharmProjects\pythonProject2\ipkvm\zt_result2.txt"
ZT = r"C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat"

results = []

# 1. ZeroTier status
try:
    r = subprocess.run([ZT, 'status'], capture_output=True, text=True, timeout=10)
    results.append(f"=== ZeroTier Status ===\n{r.stdout.strip()}\n{r.stderr.strip()}")
except Exception as e:
    results.append(f"=== ZeroTier Status ===\nERROR: {e}")

# 2. Networks
try:
    r = subprocess.run([ZT, 'listnetworks'], capture_output=True, text=True, timeout=10)
    results.append(f"=== ZeroTier Networks ===\n{r.stdout.strip()}\n{r.stderr.strip()}")
except Exception as e:
    results.append(f"=== ZeroTier Networks ===\nERROR: {e}")

# 3. Peers
try:
    r = subprocess.run([ZT, 'listpeers'], capture_output=True, text=True, timeout=10)
    results.append(f"=== ZeroTier Peers ===\n{r.stdout.strip()}\n{r.stderr.strip()}")
except Exception as e:
    results.append(f"=== ZeroTier Peers ===\nERROR: {e}")

# 4. ipconfig
try:
    r = subprocess.run(['ipconfig'], capture_output=True, text=True, timeout=10)
    lines = [l for l in r.stdout.split('\n') if 'IPv4' in l or 'ZeroTier' in l or 'Ethernet' in l.strip()[:10]]
    results.append(f"=== IPv4 Addresses ===\n" + '\n'.join(lines))
except Exception as e:
    results.append(f"=== IPv4 ===\nERROR: {e}")

# 5. Ping ZeroTier server
try:
    r = subprocess.run(['ping', '-n', '2', '10.147.17.94'], capture_output=True, text=True, timeout=15)
    results.append(f"=== Ping 10.147.17.94 (ZT Server) ===\n{r.stdout.strip()}")
except Exception as e:
    results.append(f"=== Ping ZT Server ===\nERROR: {e}")

# 6. Discovery test
try:
    import socket
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
