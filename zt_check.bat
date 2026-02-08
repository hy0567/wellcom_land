@echo off
set OUT=C:\Users\-\PycharmProjects\pythonProject2\ipkvm\zt_result.txt

echo === ZeroTier Status === > "%OUT%"
"C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat" status >> "%OUT%" 2>&1

echo. >> "%OUT%"
echo === ZeroTier Networks === >> "%OUT%"
"C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat" listnetworks >> "%OUT%" 2>&1

echo. >> "%OUT%"
echo === ZeroTier Peers === >> "%OUT%"
"C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat" listpeers >> "%OUT%" 2>&1

echo. >> "%OUT%"
echo === All IPv4 === >> "%OUT%"
ipconfig | findstr /i "IPv4" >> "%OUT%" 2>&1

echo. >> "%OUT%"
echo === Ping ZeroTier Server 10.147.17.94 === >> "%OUT%"
ping -n 2 10.147.17.94 >> "%OUT%" 2>&1

echo. >> "%OUT%"
echo === Ping KVM test === >> "%OUT%"
ping -n 2 192.168.0.1 >> "%OUT%" 2>&1

echo DONE >> "%OUT%"
