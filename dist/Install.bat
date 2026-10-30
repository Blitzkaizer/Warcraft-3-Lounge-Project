@echo off
powershell -NoProfile -Command "Unblock-File -Path '.\Warcraft3Lounge_Setup_v1.0.0.exe' -ErrorAction SilentlyContinue"
start "" ".\Warcraft3Lounge_Setup_v1.0.0.exe"

