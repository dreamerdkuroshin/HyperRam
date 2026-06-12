# install_driver.ps1 - Automated non-blocking elevated installer
$ErrorActionPreference = "Continue"

$driverDir = "C:\Users\manth\Downloads\ssd into ram\hyperram-kernel-driver"
$driverSrc = Join-Path $driverDir "HyperRAM.sys"
$driverDest = "C:\Windows\System32\drivers\HyperRAM.sys"
$ntBinPath = "\SystemRoot\System32\drivers\HyperRAM.sys"
$serviceName = "HyperRAM"
$logFile = Join-Path $driverDir "install_log.txt"

function Log($msg) {
    $time = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$time] $msg" | Out-File -FilePath $logFile -Append -Encoding utf8
    Write-Output "[$time] $msg"
}

Remove-Item $logFile -ErrorAction SilentlyContinue

Log "=== HyperRAM Automated Elevated Installer ==="

# 1. Sign
Log "[1] Signing driver..."
$certSubject = "CN=HyperRAM-Test-Cert"
$cert = Get-ChildItem Cert:\LocalMachine\My | Where-Object { $_.Subject -eq $certSubject } | Select-Object -First 1
if ($null -eq $cert) {
    Log "Creating certificate..."
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $certSubject -CertStoreLocation Cert:\LocalMachine\My -NotAfter (Get-Date).AddYears(5)
}
Log "Certificate Subject: $($cert.Subject) Thumbprint: $($cert.Thumbprint)"

Log "Trusting certificate..."
$rootStore = New-Object System.Security.Cryptography.X509Certificates.X509Store("Root", "LocalMachine")
$rootStore.Open("ReadWrite")
$rootStore.Add($cert)
$rootStore.Close()

$pubStore = New-Object System.Security.Cryptography.X509Certificates.X509Store("TrustedPublisher", "LocalMachine")
$pubStore.Open("ReadWrite")
$pubStore.Add($cert)
$pubStore.Close()

Log "Signing binary: $driverSrc"
$sig = Set-AuthenticodeSignature -FilePath $driverSrc -Certificate $cert -HashAlgorithm SHA256
Log "Signature status: $($sig.Status) ($($sig.StatusMessage))"

# 2. Stop and Delete Service
Log "[2] Stopping and deleting old service..."
$stopRes = sc.exe stop $serviceName
Log "Stop result: $stopRes"
Start-Sleep -Milliseconds 800
$delRes = sc.exe delete $serviceName
Log "Delete result: $delRes"
Start-Sleep -Milliseconds 800

# 3. Copy Driver
Log "[3] Copying driver to System32..."
Copy-Item $driverSrc $driverDest -Force
Log "Copied binary size: $((Get-Item $driverDest).Length)"

# 4. Create Service
Log "[4] Creating service..."
$createRes = sc.exe create $serviceName type= kernel start= demand error= normal binPath= $ntBinPath DisplayName= "HyperRAM"
Log "Create result: $createRes"

# 5. Set Registry Params
Log "[5] Setting registry parameters..."
$paramPath = "HKLM:\SYSTEM\CurrentControlSet\Services\HyperRAM\Parameters"
if (-not (Test-Path $paramPath)) {
    New-Item -Path $paramPath -Force
}
Set-ItemProperty -Path $paramPath -Name "PoolFilePath" -Value "\??\C:\hyperram.pool" -Type String -Force
Set-ItemProperty -Path $paramPath -Name "PoolSizeMB" -Value 256 -Type DWord -Force
Set-ItemProperty -Path $paramPath -Name "RamCacheMB" -Value 64 -Type DWord -Force
Log "Registry parameters configured successfully."

# 6. Start Service
Log "[6] Starting service..."
$startRes = sc.exe start $serviceName
Log "Start result: $startRes"

Log "=== Install Finished ==="
