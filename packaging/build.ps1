# Builds the datamosh desktop app: PyInstaller onedir into dist\datamosh\,
# and with -Installer also compiles the Inno Setup Setup.exe into dist\installer\.
#
#   .\packaging\build.ps1                # freeze only
#   .\packaging\build.ps1 -Installer     # freeze + Setup.exe (needs Inno Setup 6)
#   .\packaging\build.ps1 -Clean         # wipe build\ and dist\datamosh\ first
#
# Optional: drop ffmpeg.exe + ffprobe.exe into packaging\ffmpeg-bin\ before
# building to bundle ffmpeg with the app (mind the ffmpeg build's license).

param(
    [switch]$Installer,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

& $py -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing PyInstaller into the venv..."
    & $py -m pip install --quiet pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller failed" }
}

if ($Clean) {
    Remove-Item -Recurse -Force "$root\build", "$root\dist\datamosh" -ErrorAction SilentlyContinue
}

& $py -m PyInstaller "packaging\datamosh-ui.spec" --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
Write-Host "Frozen app: $root\dist\datamosh\datamosh.exe"

if ($Installer) {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    $iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) {
        $cmd = Get-Command iscc -ErrorAction SilentlyContinue
        if ($cmd) { $iscc = $cmd.Source }
    }
    if (-not $iscc) {
        throw "Inno Setup 6 not found -- install with: winget install JRSoftware.InnoSetup"
    }

    $m = Select-String -Path "pyproject.toml" -Pattern '^version = "(.+)"'
    $version = $m.Matches[0].Groups[1].Value

    & $iscc "/DMyAppVersion=$version" "packaging\datamosh.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }
    Write-Host "Installer: $root\dist\installer\datamosh-setup-$version.exe"
}
