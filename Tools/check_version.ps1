# check_version.ps1 - Fail if version strings disagree across the four source-of-truth files.
# Usage: .\Tools\check_version.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$pkgVer = (Get-Content "$root\package.json" | ConvertFrom-Json).version
$tauriVer = (Get-Content "$root\src-tauri\tauri.conf.json" | ConvertFrom-Json).package.version

$cargoRaw = Select-String -Path "$root\src-tauri\Cargo.toml" -Pattern '^version\s*=' | Select-Object -First 1
$cargoVer = ($cargoRaw.Line -split [char]34)[1]

$mainRaw = Select-String -Path "$root\backend\main.py" -Pattern 'version=' | Select-Object -First 1
$mainVer = ($mainRaw.Line -split 'version=')[1].TrimStart([char]34).Split([char]34)[0]

Write-Output "package.json: $pkgVer"
Write-Output "tauri.conf.json: $tauriVer"
Write-Output "Cargo.toml: $cargoVer"
Write-Output "main.py: $mainVer"

if ($pkgVer -eq $tauriVer -and $tauriVer -eq $cargoVer -and $cargoVer -eq $mainVer) {
    Write-Host "All versions agree: $pkgVer" -ForegroundColor Green
    exit 0
} else {
    Write-Host "VERSION MISMATCH DETECTED" -ForegroundColor Red
    exit 1
}
