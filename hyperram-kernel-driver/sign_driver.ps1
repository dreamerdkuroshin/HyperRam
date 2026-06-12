# HyperRAM Kernel Driver Self-Signing Script
# This script must be run as Administrator.
# It creates a local code-signing certificate, trusts it, and signs HyperRAM.sys.

Write-Output "=========================================="
Write-Output "   HyperRAM Driver Self-Signing Tool"
Write-Output "=========================================="

# 1. Check for Admin privileges
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This script MUST be run as an Administrator. Please restart PowerShell as Admin and try again."
    Exit
}

# 2. Check if the driver file exists
$driverPath = Join-Path $PSScriptRoot "HyperRAM.sys"
if (-not (Test-Path $driverPath)) {
    Write-Error "Could not find HyperRAM.sys. Please run build_driver.bat first to compile it."
    Exit
}

# 3. Create a self-signed Code Signing Certificate if it doesn't already exist
$certSubject = "CN=HyperRAM-Test-Cert"
$cert = Get-ChildItem Cert:\LocalMachine\My | Where-Object { $_.Subject -eq $certSubject } | Select-Object -First 1

if ($null -eq $cert) {
    Write-Output "[INFO] Creating new self-signed certificate..."
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $certSubject -CertStoreLocation Cert:\LocalMachine\My -NotAfter (Get-Date).AddYears(5)
} else {
    Write-Output "[INFO] Found existing test certificate."
}

# 4. Trust the certificate locally (Trusted Root & Trusted Publishers)
Write-Output "[INFO] Adding certificate to local Trusted Root Store..."
$rootStore = New-Object System.Security.Cryptography.X509Certificates.X509Store("Root", "LocalMachine")
$rootStore.Open("ReadWrite")
$rootStore.Add($cert)
$rootStore.Close()

Write-Output "[INFO] Adding certificate to Trusted Publishers..."
$pubStore = New-Object System.Security.Cryptography.X509Certificates.X509Store("TrustedPublisher", "LocalMachine")
$pubStore.Open("ReadWrite")
$pubStore.Add($cert)
$pubStore.Close()

# 5. Sign the driver binary
Write-Output "[INFO] Signing HyperRAM.sys..."
Set-AuthenticodeSignature -FilePath $driverPath -Certificate $cert -HashAlgorithm SHA256

Write-Output "[SUCCESS] Driver has been test-signed and is ready to load!"
Write-Output "=========================================="
