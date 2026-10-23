# Automated Inno Setup Compiler Script for Warcraft III Lounge
$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   Warcraft III Lounge - Installer Compiler               " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$InnoPaths = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
    "C:\Program Files\Inno Setup 5\ISCC.exe",
    (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
)

$Iscc = $null
foreach ($p in $InnoPaths) {
    if ($p -and (Test-Path $p)) {
        $Iscc = $p
        break
    }
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$IssFile = Join-Path $ScriptDir "installer.iss"
$DistApp = Join-Path (Split-Path -Parent $ScriptDir) "dist\Warcraft3Lounge\Warcraft3Lounge.exe"

if (-not (Test-Path $DistApp)) {
    Write-Warning "Application folder 'dist\Warcraft3Lounge' not found! Please run 'python build.py' first."
    exit 1
}

if ($Iscc) {
    Write-Host "Found Inno Setup Compiler at: $Iscc" -ForegroundColor Green
    Write-Host "Compiling setup installer from $IssFile..." -ForegroundColor Yellow
    & $Iscc $IssFile
    if ($LASTEXITCODE -eq 0) {
        Write-Host "SUCCESS! Installer created at: dist\Warcraft3Lounge_Setup_v1.0.0.exe" -ForegroundColor Green
    } else {
        Write-Error "Inno Setup compilation failed with exit code $LASTEXITCODE."
    }
} else {
    Write-Host ""
    Write-Warning "Inno Setup Compiler (ISCC.exe) was not found on this system."
    Write-Host "To generate 'Warcraft3Lounge_Setup_v1.0.0.exe':" -ForegroundColor Yellow
    Write-Host "1. Download and install free Inno Setup from: https://jrsoftware.org/isdl.php" -ForegroundColor Cyan
    Write-Host "2. Re-run this script: powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1" -ForegroundColor Cyan
    Write-Host "3. Alternatively, open 'installer\installer.iss' in Inno Setup GUI and click Compile (Ctrl+F9)." -ForegroundColor Cyan
}
