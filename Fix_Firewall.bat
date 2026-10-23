@echo off
:: Complete Network & Firewall Fix for Warcraft III Classic on Windows 10/11
:: Right-click this file and choose "Run as administrator"

echo ======================================================================
echo   Configuring Windows Network & Firewall for Warcraft III Classic
echo ======================================================================
echo.

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] This script MUST be run as Administrator!
    echo Please right-click Fix_Firewall.bat and choose "Run as administrator".
    echo.
    pause
    exit /b 1
)

echo [1/4] Switching Network Profile from 'Public' to 'Private'...
powershell -Command "Get-NetConnectionProfile | Set-NetConnectionProfile -NetworkCategory Private" >nul 2>&1
echo       Done! Network profile is now set to Private (allows LAN discovery).

echo.
echo [2/4] Removing any legacy or blocking rules...
netsh advfirewall firewall delete rule name="Warcraft III" >nul 2>&1
netsh advfirewall firewall delete rule name="Frozen Throne" >nul 2>&1
netsh advfirewall firewall delete rule name="warcraft3lounge" >nul 2>&1
netsh advfirewall firewall delete rule name="Warcraft III Lounge" >nul 2>&1
netsh advfirewall firewall delete rule name="Warcraft III Lounge Portable" >nul 2>&1
netsh advfirewall firewall delete rule name="Warcraft III Ports UDP" >nul 2>&1
netsh advfirewall firewall delete rule name="Warcraft III Ports TCP" >nul 2>&1
echo       Done!

echo.
echo [3/4] Adding Inbound Allow rules for war3.exe and Frozen Throne.exe...
netsh advfirewall firewall add rule name="Warcraft III" dir=in action=allow program="F:\Warcraft III\war3.exe" enable=yes profile=any
netsh advfirewall firewall add rule name="Frozen Throne" dir=in action=allow program="F:\Warcraft III\Frozen Throne.exe" enable=yes profile=any
netsh advfirewall firewall add rule name="Warcraft III Lounge" dir=in action=allow program="E:\Warcraft 3 Lounge Project\dist\Warcraft3Lounge\Warcraft3Lounge.exe" enable=yes profile=any
netsh advfirewall firewall add rule name="Warcraft III Lounge Portable" dir=in action=allow program="E:\Warcraft 3 Lounge Project\dist\Warcraft3Lounge_Portable.exe" enable=yes profile=any
echo       Done!

echo.
echo [4/4] Opening UDP & TCP Ports (6112 - 6115)...
netsh advfirewall firewall add rule name="Warcraft III Ports UDP" dir=in action=allow protocol=UDP localport=6112-6115 enable=yes profile=any
netsh advfirewall firewall add rule name="Warcraft III Ports TCP" dir=in action=allow protocol=TCP localport=6112-6115 enable=yes profile=any
echo       Done!

echo.
echo ======================================================================
echo   ALL FIXES APPLIED SUCCESSFULLY!
echo   1. Network profile is now Private.
echo   2. Windows Defender Firewall allows Warcraft III on all networks.
echo   3. Port 6112 is completely open.
echo ======================================================================
echo.
pause
