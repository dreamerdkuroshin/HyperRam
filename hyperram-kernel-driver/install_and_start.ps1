$ErrorActionPreference = "Stop"

$root         = Split-Path -Parent $MyInvocation.MyCommand.Path
$log          = Join-Path $root "hyperram-install-output.txt"
$driver       = Join-Path $root "HyperRAM.sys"
$systemDriver = "C:\Windows\System32\drivers\HyperRAM.sys"
$ntBinPath    = "\SystemRoot\System32\drivers\HyperRAM.sys"   # absolute NT path required by SCM

function Log($msg) { $msg | Tee-Object -FilePath $log -Append }

Remove-Item $log -Force -ErrorAction SilentlyContinue
Log "=========================================="
Log " HyperRAM WDM Driver - Install & Start"
Log "=========================================="

# ---- 1. Sign ---------------------------------------------------------------
Log "[INFO] Signing driver..."
& (Join-Path $root "sign_driver.ps1") *>&1 | Tee-Object -FilePath $log -Append

# ---- 2. Copy ---------------------------------------------------------------
Log "[INFO] Copying $driver -> $systemDriver"
Copy-Item $driver $systemDriver -Force
Log "[INFO] Size: $((Get-Item $systemDriver).Length) bytes"

# ---- 3. Delete stale service & recreate with correct path -----------------
Log "[INFO] Removing stale service registration (if any)..."
sc.exe stop   HyperRAM 2>&1 | Out-Null
Start-Sleep -Milliseconds 600
sc.exe delete HyperRAM 2>&1 | Out-Null
Start-Sleep -Milliseconds 600

Log "[INFO] Creating service with absolute NT ImagePath..."
$r = sc.exe create HyperRAM type= kernel start= demand error= normal binPath= $ntBinPath DisplayName= "HyperRAM"
Log $r

# Verify registry
Log "[INFO] Registry check:"
reg query "HKLM\SYSTEM\CurrentControlSet\Services\HyperRAM" /s | Tee-Object -FilePath $log -Append

# ---- 4. Start --------------------------------------------------------------
Log "[INFO] Starting service..."
$startOut = sc.exe start HyperRAM
Log $startOut
Start-Sleep -Seconds 2

# ---- 5. Query --------------------------------------------------------------
Log "[INFO] Querying service state..."
sc.exe query HyperRAM | Tee-Object -FilePath $log -Append

# ---- 6. Kernel log ---------------------------------------------------------
$klog = "C:\Windows\Temp\hyperram.log"
Log "[INFO] Looking for kernel log at $klog..."
Start-Sleep -Milliseconds 500

if (Test-Path $klog) {
    Log "[SUCCESS] === Kernel Log ==="
    Get-Content $klog | Tee-Object -FilePath $log -Append
} else {
    Log "[WARNING] Kernel log absent - DriverEntry did not write it."
    Log "[INFO] Dumping recent System events..."
    Get-WinEvent -LogName System -MaxEvents 20 |
        Where-Object { $_.Id -in @(7000,7026,7045) -and $_.TimeCreated -gt (Get-Date).AddMinutes(-5) } |
        Select-Object TimeCreated,Id,Message | Format-List |
        Out-String | Tee-Object -FilePath $log -Append
}

Log ""
Log "[DONE] See $log for full output."
