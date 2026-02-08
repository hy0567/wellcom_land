@echo off
title WellcomLAND Network Priority Fix

:: ============================================
:: Step 1: Check admin rights
:: ============================================
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting admin rights...
    :: Try PowerShell UAC elevation
    powershell -Command "Start-Process '%~f0' -Verb RunAs" >nul 2>&1
    if %errorlevel% neq 0 (
        :: PowerShell also blocked - try VBS
        echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\_uac.vbs"
        echo UAC.ShellExecute "%~f0", "", "", "runas", 1 >> "%temp%\_uac.vbs"
        cscript //nologo "%temp%\_uac.vbs" >nul 2>&1
        del "%temp%\_uac.vbs" >nul 2>&1
    )
    exit /b
)

echo.
echo ========================================
echo  WellcomLAND Network Priority Fix v1.1
echo ========================================
echo.

:: ============================================
:: Step 2: Try netsh (most common method)
:: ============================================
set NETSH_OK=0
netsh interface ipv4 show interfaces >nul 2>&1
if %errorlevel%==0 set NETSH_OK=1

if %NETSH_OK%==1 (
    echo [Method: netsh]
    echo.
    echo [Before]
    netsh interface ipv4 show interfaces
    echo.

    :: Tailscale - low priority
    netsh interface ipv4 set interface "Tailscale" metric=1000 >nul 2>&1
    if %errorlevel%==0 (echo [OK] Tailscale metric=1000) else (echo [SKIP] Tailscale not found)

    :: Common LAN adapter names - high priority
    for %%N in ("Ethernet" "Ethernet 2" "Ethernet 3" "Wi-Fi" "Local Area Connection" "LAN") do (
        netsh interface ipv4 set interface %%N metric=5 >nul 2>&1
        if not errorlevel 1 echo [OK] %%N metric=5
    )

    :: Auto-detect by index: find 192.168.x or 10.x adapters
    for /f "skip=3 tokens=1,4" %%a in ('netsh interface ipv4 show interfaces') do (
        if "%%b"=="connected" (
            netsh interface ipv4 show address %%a 2>nul | findstr "192.168. 10." >nul 2>&1
            if not errorlevel 1 (
                netsh interface ipv4 set interface interface=%%a metric=5 >nul 2>&1
                echo [OK] Interface idx=%%a metric=5
            )
        )
    )

    echo.
    echo [After]
    netsh interface ipv4 show interfaces
    echo.
    echo === Done! LAN is now primary ===
    echo.
    pause
    exit /b
)

:: ============================================
:: Step 3: netsh blocked - try WMIC
:: ============================================
set WMIC_OK=0
wmic os get caption >nul 2>&1
if %errorlevel%==0 set WMIC_OK=1

if %WMIC_OK%==1 (
    echo [Method: WMIC] netsh is blocked, using WMIC...
    echo.

    :: Show current adapters
    echo [Current Network Adapters]
    wmic nicconfig where "IPEnabled=TRUE" get Description,IPAddress,IPConnectionMetric /format:list 2>nul
    echo.

    :: Find Tailscale adapter index and set high metric
    for /f "tokens=2 delims==" %%i in ('wmic nicconfig where "IPEnabled=TRUE and Description like '%%Tailscale%%'" get Index /value 2^>nul ^| findstr "="') do (
        wmic nicconfig where "Index=%%i" call SetIPConnectionMetric 1000 >nul 2>&1
        echo [OK] Tailscale metric=1000
    )

    :: Find LAN adapters (192.168.x or 10.x) and set low metric
    for /f "tokens=2 delims==" %%i in ('wmic nicconfig where "IPEnabled=TRUE" get Index /value 2^>nul ^| findstr "="') do (
        for /f "tokens=*" %%a in ('wmic nicconfig where "Index=%%i" get IPAddress /value 2^>nul ^| findstr "192.168. 10."') do (
            wmic nicconfig where "Index=%%i" call SetIPConnectionMetric 5 >nul 2>&1
            echo [OK] Adapter idx=%%i metric=5
        )
    )

    echo.
    echo [After - Updated Adapters]
    wmic nicconfig where "IPEnabled=TRUE" get Description,IPAddress,IPConnectionMetric /format:list 2>nul
    echo.
    echo === Done! ===
    echo.
    pause
    exit /b
)

:: ============================================
:: Step 4: Both netsh and WMIC blocked
:: Try standalone EXE (no Python needed)
:: ============================================
echo [WARNING] netsh and WMIC are both blocked on this PC.
echo.

:: Try EXE version first (no dependencies)
if exist "%~dp0fix_network_priority.exe" (
    echo Trying EXE method...
    "%~dp0fix_network_priority.exe"
    pause
    exit /b
)

:: Try Python if available
where python >nul 2>&1
if %errorlevel%==0 (
    if exist "%~dp0fix_network_priority.py" (
        echo Trying Python method...
        python "%~dp0fix_network_priority.py"
        pause
        exit /b
    )
)

echo.
echo ==========================================
echo  All methods failed (netsh/WMIC/EXE)
echo ==========================================
echo.
echo  This PC blocks network commands.
echo  WellcomLAND handles IP priority internally,
echo  so the app itself will work correctly.
echo.
echo  If other programs need LAN priority,
echo  contact your system administrator.
echo.
pause
