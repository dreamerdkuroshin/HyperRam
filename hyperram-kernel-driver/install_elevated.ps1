# ============================================================================
#  HyperRAM Driver — Self-Elevating Installer
#  This script re-launches itself as Administrator if not already elevated.
# ============================================================================

# --- Step 0: Self-elevate if not admin ---
$principal = [Security.Principal.WindowsPrincipal]::new(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[INFO] Not running as admin — requesting elevation..." -ForegroundColor Yellow
    $scriptPath = $MyInvocation.MyCommand.Path
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$scriptPath`""
    )
    exit 0
}

# --- We are now running elevated ---
Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   HyperRAM Driver — Elevated Installer"    -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

$driverDir    = "c:\Users\manth\Downloads\ssd into ram\hyperram-kernel-driver"
$driverSrc    = Join-Path $driverDir "HyperRAM.sys"
$driverDest   = "C:\Windows\System32\drivers\HyperRAM.sys"
$ntBinPath    = "\SystemRoot\System32\drivers\HyperRAM.sys"
$serviceName  = "HyperRAM"

# --- Step 1: Check test-signing ---
Write-Host ""
Write-Host "[1/6] Checking test-signing mode..." -ForegroundColor White
$bcdOutput = & bcdedit /enum 2>&1 | Out-String
if ($bcdOutput -match "testsigning\s+Yes") {
    Write-Host "  Test signing: ENABLED" -ForegroundColor Green
} else {
    Write-Host "  Test signing: NOT ENABLED" -ForegroundColor Yellow
    Write-Host "  WARNING: Driver may fail to load. Run:" -ForegroundColor Yellow
    Write-Host "    bcdedit /set testsigning on" -ForegroundColor White
    Write-Host "  Then reboot and re-run this script." -ForegroundColor Yellow
}

# --- Step 2: Sign driver ---
Write-Host ""
Write-Host "[2/6] Signing driver..." -ForegroundColor White
$signScript = Join-Path $driverDir "sign_driver.ps1"
if (Test-Path $signScript) {
    & $signScript
} else {
    Write-Host "  sign_driver.ps1 not found, skipping." -ForegroundColor Yellow
}

# --- Step 3: Copy driver ---
Write-Host ""
Write-Host "[3/6] Copying $driverSrc -> $driverDest" -ForegroundColor White
Copy-Item $driverSrc $driverDest -Force
$sz = (Get-Item $driverDest).Length
Write-Host "  Copied: $sz bytes" -ForegroundColor Green

# --- Step 4: Stop/delete old service ---
Write-Host ""
Write-Host "[4/6] Removing old service (if exists)..." -ForegroundColor White
sc.exe stop $serviceName 2>&1 | Out-Null
Start-Sleep -Milliseconds 800
sc.exe delete $serviceName 2>&1 | Out-Null
Start-Sleep -Milliseconds 800

# --- Step 5: Create + configure service ---
Write-Host ""
Write-Host "[5/6] Creating kernel service..." -ForegroundColor White
$createResult = sc.exe create $serviceName type= kernel start= demand error= normal binPath= $ntBinPath DisplayName= "HyperRAM"
Write-Host "  $createResult"

# Set registry parameters for pool file path
$paramPath = "HKLM:\SYSTEM\CurrentControlSet\Services\HyperRAM\Parameters"
if (-not (Test-Path $paramPath)) {
    New-Item -Path $paramPath -Force | Out-Null
}
# Default pool file path — change this to point at any NVMe
$defaultPoolPath = "\??\C:\hyperram.pool"
if (-not (Get-ItemProperty -Path $paramPath -Name "PoolFilePath" -ErrorAction SilentlyContinue)) {
    New-ItemProperty -Path $paramPath -Name "PoolFilePath" -Value $defaultPoolPath -PropertyType String -Force | Out-Null
    Write-Host "  Registry: PoolFilePath = $defaultPoolPath" -ForegroundColor Green
} else {
    $existingPath = (Get-ItemProperty -Path $paramPath -Name "PoolFilePath").PoolFilePath
    Write-Host "  Registry: PoolFilePath = $existingPath (existing)" -ForegroundColor Green
}

# Default pool size in MB
if (-not (Get-ItemProperty -Path $paramPath -Name "PoolSizeMB" -ErrorAction SilentlyContinue)) {
    New-ItemProperty -Path $paramPath -Name "PoolSizeMB" -Value 256 -PropertyType DWord -Force | Out-Null
    Write-Host "  Registry: PoolSizeMB = 256" -ForegroundColor Green
} else {
    $existingSize = (Get-ItemProperty -Path $paramPath -Name "PoolSizeMB").PoolSizeMB
    Write-Host "  Registry: PoolSizeMB = $existingSize MB (existing)" -ForegroundColor Green
}

# --- Step 6: Start service ---
Write-Host ""
Write-Host "[6/6] Starting HyperRAM service..." -ForegroundColor White
$startResult = sc.exe start $serviceName
Write-Host "  $startResult"

Start-Sleep -Seconds 2

# --- Final status ---
Write-Host ""
Write-Host "========== Service Status ==========" -ForegroundColor Cyan
sc.exe query $serviceName

# Check kernel log
$klog = "C:\Windows\Temp\hyperram.log"
if (Test-Path $klog) {
    Write-Host ""
    Write-Host "========== Kernel Log ==========" -ForegroundColor Cyan
    Get-Content $klog -Tail 20
}

Write-Host ""
Write-Host "Done. Press any key to close..." -ForegroundColor Green
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
