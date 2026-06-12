# Self-elevating driver update + validation runner
# Double-click or run from any PowerShell window - it will auto-elevate via UAC

param([switch]$Elevated)

if (-not $Elevated) {
    $scriptPath = $MyInvocation.MyCommand.Path
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -File `"$scriptPath`" -Elevated"
    exit
}

$ErrorActionPreference = 'Continue'
$driverDir  = "c:\Users\manth\Downloads\ssd into ram\hyperram-kernel-driver"
$daemonDir  = "c:\Users\manth\Downloads\ssd into ram\hyperram-daemon"
$resultsDir = "$daemonDir\results\validation"
$logFile    = "$resultsDir\validation_suite.log"
$newSys     = "$driverDir\HyperRAM.sys"
$sysTarget  = "C:\Windows\System32\drivers\HyperRAM.sys"
$pyExe      = "$daemonDir\venv\Scripts\python.exe"

New-Item -ItemType Directory -Force -Path $resultsDir | Out-Null

function Write-Log($msg) {
    $ts = Get-Date -Format "HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Host $line
    Add-Content -Path $logFile -Value $line
}

Write-Log "=== HyperRAM Driver Update + Validation Suite ==="
Write-Log "New driver : $newSys"
Write-Log "Log file   : $logFile"

# --- Stop driver ---
Write-Log "[1/5] Stopping HyperRAM driver..."
& sc.exe stop HyperRAM | Out-Null
Start-Sleep -Seconds 2

$state = (& sc.exe query HyperRAM | Select-String 'STATE').ToString().Trim()
Write-Log "      State after stop: $state"

# --- Sign driver ---
Write-Log "[1.5/5] Signing updated HyperRAM.sys..."
$signScript = Join-Path $driverDir "sign_driver.ps1"
if (Test-Path $signScript) {
    & $signScript | Out-Null
    Write-Log "      Signed."
}

# --- Copy new binary ---
Write-Log "[2/5] Copying updated HyperRAM.sys..."
try {
    Copy-Item -Force $newSys $sysTarget
    $srcSize = (Get-Item $newSys).Length
    $dstSize = (Get-Item $sysTarget).Length
    if ($srcSize -eq $dstSize) {
        Write-Log "      OK — $dstSize bytes copied."
    } else {
        Write-Log "      WARNING — size mismatch! src=$srcSize dst=$dstSize"
    }
} catch {
    Write-Log "      ERROR copying driver: $_"
    pause; exit 1
}

# --- Start driver ---
Write-Log "[3/5] Starting updated HyperRAM driver..."
& sc.exe start HyperRAM | Out-Null
Start-Sleep -Seconds 2

$query = & sc.exe query HyperRAM
if ($query -match 'RUNNING') {
    Write-Log "      Driver is RUNNING."
} else {
    Write-Log "      ERROR: Driver did not start!"
    $query | ForEach-Object { Write-Log "      $_" }
    pause; exit 1
}

# --- Standard validation ---
Write-Log ""
Write-Log "[4/5] Running validation suite (run_validation.py)..."
Write-Log "      Writing 10,000 random pages + SHA-256 integrity check..."
& $pyExe "$daemonDir\run_validation.py" --recovery 2>&1 | ForEach-Object { Write-Log "      $_" }

# --- Latency benchmark ---
Write-Log ""
Write-Log "[5/5] Running cold latency benchmark..."
& $pyExe "$daemonDir\kernel_benchmark.py" --pages 2000 --reads 10000 --cold --kernel-only 2>&1 | ForEach-Object { Write-Log "      $_" }

Write-Log ""
Write-Log "=== ALL DONE ==="
Write-Log "Results saved to: $logFile"

Write-Host ""
Write-Host "Press any key to close..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
