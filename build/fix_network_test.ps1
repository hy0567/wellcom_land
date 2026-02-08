# Quick test - show current state only
Write-Host "=== Current Network State ===" -ForegroundColor Cyan
Get-NetIPInterface -AddressFamily IPv4 | Where-Object { $_.ConnectionState -eq 'Connected' } | Select-Object InterfaceAlias, InterfaceMetric | Format-Table -AutoSize
Write-Host "=== IP Addresses ===" -ForegroundColor Cyan
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' } | Select-Object InterfaceAlias, IPAddress | Format-Table -AutoSize
Write-Host "=== Default Route ===" -ForegroundColor Cyan
Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object InterfaceAlias, NextHop, RouteMetric | Format-Table -AutoSize
