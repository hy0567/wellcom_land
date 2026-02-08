# WellcomLAND Network Priority Fix
# LAN (192.168.x.x) first, Tailscale/APIPA last
# Run as Administrator

Write-Host "=== WellcomLAND Network Priority ===" -ForegroundColor Cyan
Write-Host ""

# Check admin
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] Run as Administrator!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Before
Write-Host "--- Before ---" -ForegroundColor Yellow
Get-NetIPInterface -AddressFamily IPv4 | Where-Object { $_.ConnectionState -eq 'Connected' } | Select-Object InterfaceAlias, InterfaceMetric | Format-Table -AutoSize

# 1. Find LAN adapters (192.168.x.x or 10.x.x.x)
$lanAdapters = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -match '^192\.168\.' -or $_.IPAddress -match '^10\.' }

if ($lanAdapters) {
    foreach ($adapter in $lanAdapters) {
        $alias = $adapter.InterfaceAlias
        Write-Host "LAN: $alias ($($adapter.IPAddress)) -> Metric 5" -ForegroundColor Green
        Set-NetIPInterface -InterfaceAlias $alias -InterfaceMetric 5
    }
} else {
    Write-Host "[WARN] No 192.168.x.x or 10.x.x.x adapter found" -ForegroundColor Yellow
}

# 2. Tailscale -> low priority
$tailscale = Get-NetIPInterface -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -like '*Tailscale*' }
if ($tailscale) {
    Set-NetIPInterface -InterfaceAlias 'Tailscale' -InterfaceMetric 1000
    Write-Host "Tailscale -> Metric 1000" -ForegroundColor Yellow
}

# 3. APIPA (169.254.x.x) -> lowest priority
$apipaAdapters = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -match '^169\.254\.' }
if ($apipaAdapters) {
    foreach ($adapter in $apipaAdapters) {
        $alias = $adapter.InterfaceAlias
        Set-NetIPInterface -InterfaceAlias $alias -InterfaceMetric 2000
        Write-Host "APIPA ($alias) -> Metric 2000" -ForegroundColor DarkYellow
    }
}

Write-Host ""
Write-Host "--- After ---" -ForegroundColor Cyan
Get-NetIPInterface -AddressFamily IPv4 | Where-Object { $_.ConnectionState -eq 'Connected' } | Select-Object InterfaceAlias, InterfaceMetric | Format-Table -AutoSize

Write-Host "--- Default Route ---" -ForegroundColor Cyan
Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object InterfaceAlias, NextHop, RouteMetric | Format-Table -AutoSize

Write-Host "=== Done! 192.168.x.x is now primary ===" -ForegroundColor Green
Read-Host "Press Enter to exit"
