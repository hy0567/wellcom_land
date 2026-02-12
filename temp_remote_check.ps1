# Remote PC check script
$ip = "100.93.95.114"

# Try to access via admin share
try {
    $logPath = "\\$ip\C$\WellcomLAND\logs\app.log"
    if (Test-Path $logPath) {
        Write-Host "=== app.log (last 100 lines) ==="
        Get-Content $logPath -Tail 100
    } else {
        Write-Host "Cannot access $logPath"
    }
} catch {
    Write-Host "Admin share not accessible: $_"
}

# Try data dir
try {
    $dataPath = "\\$ip\C$\WellcomLAND\data"
    if (Test-Path $dataPath) {
        Write-Host "`n=== data dir ==="
        Get-ChildItem $dataPath | Format-Table Name, Length, LastWriteTime
    }
} catch {
    Write-Host "Data dir not accessible: $_"
}

# Try fault.log
try {
    $faultPath = "\\$ip\C$\WellcomLAND\logs\fault.log"
    if (Test-Path $faultPath) {
        Write-Host "`n=== fault.log (last 50 lines) ==="
        Get-Content $faultPath -Tail 50
    }
} catch {
    Write-Host "fault.log not accessible: $_"
}

# version check
try {
    $versionJson = "\\$ip\C$\WellcomLAND\app\version.json"
    $versionPy = "\\$ip\C$\WellcomLAND\app\version.py"

    Write-Host "`n=== Version files ==="
    if (Test-Path $versionJson) {
        Write-Host "version.json:"
        Get-Content $versionJson
    } else {
        Write-Host "version.json NOT FOUND at $versionJson"
    }

    if (Test-Path $versionPy) {
        Write-Host "version.py:"
        Get-Content $versionPy
    } else {
        Write-Host "version.py NOT FOUND at $versionPy"
    }
} catch {
    Write-Host "Version check failed: $_"
}
