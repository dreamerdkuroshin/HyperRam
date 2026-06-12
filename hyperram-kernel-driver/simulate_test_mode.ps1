param(
    [switch]$VerboseChecks
)

$ErrorActionPreference = "Continue"
$driverDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$driverPath = Join-Path $driverDir "HyperRAM.sys"
$infPath = Join-Path $driverDir "HyperRAM.inf"

function Write-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host "== $Text ==" -ForegroundColor Cyan
}

function Write-Result {
    param(
        [string]$Name,
        [bool]$Ok,
        [string]$Detail
    )

    $status = if ($Ok) { "OK" } else { "WARN" }
    $color = if ($Ok) { "Green" } else { "Yellow" }
    Write-Host ("[{0}] {1}: {2}" -f $status, $Name, $Detail) -ForegroundColor $color
}

Write-Host "=========================================="
Write-Host " HyperRAM Kernel Driver Simulated Test Mode"
Write-Host "=========================================="
Write-Host "This is a dry run. It will not change boot settings, create services, or load HyperRAM.sys."

Write-Step "Artifact checks"
$driverExists = Test-Path -LiteralPath $driverPath
Write-Result "Driver binary" $driverExists $driverPath

if ($driverExists) {
    $driverItem = Get-Item -LiteralPath $driverPath
    Write-Result "Driver size" ($driverItem.Length -gt 0) ("{0} bytes" -f $driverItem.Length)

    $signature = Get-AuthenticodeSignature -LiteralPath $driverPath
    Write-Result "Driver signature" ($signature.Status -ne "NotSigned") $signature.Status
}

$infExists = Test-Path -LiteralPath $infPath
Write-Result "INF file" $infExists $infPath

Write-Step "Environment checks"
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Result "Administrator shell" $isAdmin $(if ($isAdmin) { "current shell is elevated" } else { "real install must run from Administrator PowerShell" })

$testSigningOutput = & bcdedit /enum 2>$null
$testSigningEnabled = $false
if ($LASTEXITCODE -eq 0 -and $testSigningOutput) {
    $testSigningEnabled = ($testSigningOutput | Select-String -Pattern "testsigning\s+Yes" -Quiet)
    Write-Result "Test signing query" $true $(if ($testSigningEnabled) { "testsigning is already ON" } else { "testsigning is not currently ON" })
} else {
    Write-Result "Test signing query" $false "bcdedit query failed; real install must run elevated"
}

$serviceOutput = & sc.exe query HyperRAM 2>&1
$serviceExists = $LASTEXITCODE -eq 0
Write-Result "HyperRAM service" $serviceExists $(if ($serviceExists) { "service already exists" } else { "service is not installed yet" })

if ($VerboseChecks) {
    Write-Step "Raw service query"
    $serviceOutput | ForEach-Object { Write-Host $_ }
}

Write-Step "Real commands to apply later"
Write-Host "1. Enable Test Mode, then reboot:"
Write-Host "   bcdedit /set testsigning on" -ForegroundColor White
Write-Host ""
Write-Host "2. After reboot, install and start the driver:"
Write-Host ('   cd "{0}"' -f $driverDir) -ForegroundColor White
Write-Host ('   sc.exe create HyperRAM type= kernel start= demand binPath= "{0}"' -f $driverPath) -ForegroundColor White
Write-Host "   sc.exe start HyperRAM" -ForegroundColor White
Write-Host "   sc.exe query HyperRAM" -ForegroundColor White
Write-Host ""
Write-Host "3. Cleanup commands:"
Write-Host "   sc.exe stop HyperRAM" -ForegroundColor White
Write-Host "   sc.exe delete HyperRAM" -ForegroundColor White
Write-Host "   bcdedit /set testsigning off" -ForegroundColor White

Write-Step "Simulated result"
if ($driverExists -and $infExists) {
    Write-Host "PASS: HyperRAM.sys and HyperRAM.inf are ready for a real Test Mode install." -ForegroundColor Green
} else {
    Write-Host "HOLD: Build artifacts are missing. Run build_driver.bat before a real install." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "No system changes were made."
