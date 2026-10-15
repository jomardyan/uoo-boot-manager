[CmdletBinding()]
param([ValidateSet('x64','arm64')][string]$Architecture = 'x64')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$version = ([xml](Get-Content (Join-Path $repo 'windows/Directory.Build.props') -Raw)).Project.PropertyGroup.Version
$installer = Join-Path $repo "dist/windows/Uoo-Boot-Manager-$version-windows-$Architecture-setup.exe"
$destination = Join-Path $env:ProgramFiles 'Uoo Boot Manager'
$pictures = Join-Path $repo "build/windows/screenshots-$Architecture"
function Invoke-Checked([string]$File, [string[]]$Arguments) {
    $process = Start-Process $File -ArgumentList $Arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "$File failed ($($process.ExitCode))." }
}
# The disposable CI runner is already elevated. Install/uninstall must not alter its system BCD.
$before = (& "$env:SystemRoot/System32/bcdedit.exe" /enum all /v 2>&1 | Out-String)
Invoke-Checked $installer @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART')
try {
    if (!(Test-Path (Join-Path $destination 'Uoo.BootHelper.exe'))) { throw 'Installed helper is missing.' }
    Invoke-Checked (Join-Path $destination 'Uoo.BootManager.exe') @('--smoke-test', ('"' + $pictures + '"'))
    if (@(Get-ChildItem $pictures -Filter '*.png').Count -lt 7) { throw 'Native desktop did not render all seven pages.' }
    Invoke-Checked (Join-Path $destination 'Uoo.BootManager.exe') @('--inspect-test', ('"' + (Join-Path $pictures 'installed-inspection.json') + '"'))
    $inspection = Get-Content (Join-Path $pictures 'installed-inspection.json') -Raw | ConvertFrom-Json
    if (!$inspection.success -or @($inspection.snapshot.entries | Where-Object { $_.isCurrent -and $_.isWindows }).Count -ne 1) { throw 'Installed administrator helper could not inspect the current loader.' }
} finally {
    $uninstaller = Join-Path $destination 'unins000.exe'
    if (Test-Path $uninstaller) { Invoke-Checked $uninstaller @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') }
}
$after = (& "$env:SystemRoot/System32/bcdedit.exe" /enum all /v 2>&1 | Out-String)
if ($before -ne $after) { throw 'Install/demo/uninstall changed the runner BCD store.' }
if (Test-Path (Join-Path $destination 'Uoo.BootManager.exe')) { throw 'Uninstall left the application executable installed.' }
Write-Output 'Native desktop installation, demo pages, and uninstall passed; system BCD unchanged.'
