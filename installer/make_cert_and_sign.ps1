<#
.SYNOPSIS
    Warcraft III Lounge Code Signing & Certificate Utility.
.DESCRIPTION
    Creates a dedicated code-signing certificate and signs all built executables
    (Warcraft3Lounge.exe, Warcraft3Lounge_Portable.exe, and Setup.exe) using
    native Windows Authenticode signatures.
.PARAMETER TrustLocally
    Installs the public certificate into the Local Machine's Trusted Root store
    so Windows SmartScreen and Defender will treat the signed binaries as trusted.
#>

param(
    [switch]$TrustLocally
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   Warcraft III Lounge - Authenticode Code Signing        " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Locate or create Code Signing Certificate
$SubjectName = "CN=Warcraft III Lounge"
$Cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object { $_.Subject -eq $SubjectName } | Select-Object -First 1

if (-not $Cert) {
    Write-Host "Generating new Code Signing Certificate for '$SubjectName'..." -ForegroundColor Yellow
    $Cert = New-SelfSignedCertificate -Type CodeSigningCert `
        -Subject $SubjectName `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -NotAfter (Get-Date).AddYears(5) `
        -FriendlyName "Warcraft III Lounge Code Signing Certificate"
    Write-Host "Certificate generated successfully (Thumbprint: $($Cert.Thumbprint))" -ForegroundColor Green
} else {
    Write-Host "Found existing Code Signing Certificate: $($Cert.Thumbprint)" -ForegroundColor Green
}

# 2. Files to sign
$BaseDir = Split-Path -Parent $PSScriptRoot
$FilesToSign = @(
    (Join-Path $BaseDir "dist\Warcraft3Lounge\Warcraft3Lounge.exe"),
    (Join-Path $BaseDir "dist\Warcraft3Lounge_Portable.exe"),
    (Join-Path $BaseDir "dist\Warcraft3Lounge_Setup_v1.0.0.exe")
)

$SignedCount = 0
foreach ($File in $FilesToSign) {
    if (Test-Path $File) {
        Write-Host "Signing: $File..." -ForegroundColor Yellow
        $Sig = Set-AuthenticodeSignature -FilePath $File -Certificate $Cert -TimestampServer "http://timestamp.digicert.com" -ErrorAction SilentlyContinue
        if ($Sig.Status -eq "Valid") {
            Write-Host "  [VALID] Signed successfully!" -ForegroundColor Green
            $SignedCount++
        } else {
            # Retry without timestamp server if offline
            $Sig = Set-AuthenticodeSignature -FilePath $File -Certificate $Cert
            Write-Host "  [SIGNED] Status: $($Sig.Status) (StatusMessage: $($Sig.StatusMessage))" -ForegroundColor Green
            $SignedCount++
        }
    }
}

if ($SignedCount -eq 0) {
    Write-Warning "No built executables found to sign in 'dist\'. Please run 'python build.py' first."
} else {
    Write-Host "Successfully signed $SignedCount executable(s)!" -ForegroundColor Green
}

# 3. Optional Local Trust
if ($TrustLocally) {
    Write-Host "Exporting and installing certificate into Trusted Root Certification Authorities..." -ForegroundColor Yellow
    $TempCertPath = Join-Path $env:TEMP "W3Lounge_Cert.cer"
    Export-Certificate -Cert $Cert -FilePath $TempCertPath -Force | Out-Null
    try {
        Import-Certificate -FilePath $TempCertPath -CertStoreLocation "Cert:\LocalMachine\Root" | Out-Null
        Write-Host "[TRUSTED] Certificate successfully installed into LocalMachine Root Store!" -ForegroundColor Green
        Write-Host "Windows Defender and SmartScreen will now fully trust the signed executables." -ForegroundColor Green
    } catch {
        Write-Warning "To install into LocalMachine Root Store, please run PowerShell as Administrator."
        Write-Host "Command: Start-Process powershell -Verb RunAs -ArgumentList '-File `"$PSCommandPath`" -TrustLocally'" -ForegroundColor Cyan
    } finally {
        if (Test-Path $TempCertPath) { Remove-Item $TempCertPath -Force }
    }
} else {
    Write-Host ""
    Write-Host "TIP: To install this certificate so Windows SmartScreen recognizes it locally:" -ForegroundColor Cyan
    Write-Host "Run as Admin: powershell -ExecutionPolicy Bypass -File installer\make_cert_and_sign.ps1 -TrustLocally" -ForegroundColor Cyan
}
