[CmdletBinding()]
param([string]$Destination = (Join-Path $env:RUNNER_TEMP 'uoo-inno'))
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Official upstream release, with its SHA256 pinned independently of redirects.
$uri = 'https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe'
$sha256 = '0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f'
$download = Join-Path ([IO.Path]::GetTempPath()) ('uoo-inno-' + [Guid]::NewGuid().ToString('N') + '.exe')
try {
    Invoke-WebRequest -Uri $uri -OutFile $download
    if ((Get-FileHash $download -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha256) { throw 'Inno Setup checksum mismatch.' }
    $process = Start-Process $download -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/DIR="' + $Destination + '"')) -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Inno Setup installation failed ($($process.ExitCode))." }
    $compiler = Join-Path $Destination 'ISCC.exe'
    if (!(Test-Path $compiler)) { throw 'Inno Setup compiler is missing.' }
    if ($env:GITHUB_ENV) { "UOO_INNO_COMPILER=$compiler" | Out-File $env:GITHUB_ENV -Encoding utf8 -Append }
    Write-Output $compiler
} finally { Remove-Item $download -Force -ErrorAction SilentlyContinue }
