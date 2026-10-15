[CmdletBinding()]
param(
    [ValidateSet('win-x64','win-arm64')][string]$Runtime = 'win-x64',
    [string]$InnoCompiler = '',
    [switch]$SkipInstaller
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$version = ([xml](Get-Content (Join-Path $repo 'windows/Directory.Build.props') -Raw)).Project.PropertyGroup.Version
$cpu = $Runtime.Substring(4)
$stage = Join-Path $repo "build/windows/$Runtime"
$payload = Join-Path $stage 'payload'
$output = Join-Path $repo 'dist/windows'
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item $payload -ItemType Directory -Force | Out-Null
New-Item $output -ItemType Directory -Force | Out-Null
# WPF carries the full WindowsBase/System.CodeDom implementations. Its runtime
# files must win over the plain helper framework's smaller compatibility facades.
foreach ($project in @('Uoo.Helper','Uoo.Desktop')) {
    $publish = Join-Path $stage $project
    & dotnet publish (Join-Path $repo "windows/$project/$project.csproj") -c Release -r $Runtime --self-contained true --output $publish --nologo -p:PublishSingleFile=false -p:DebugType=None -p:DebugSymbols=false
    if ($LASTEXITCODE -ne 0) { throw "Publishing $project failed." }
    Copy-Item "$publish/*" $payload -Recurse -Force
}
foreach ($name in @('Uoo.BootManager.exe','Uoo.BootHelper.exe','Uoo.Core.dll','Uoo.Platform.dll','System.Management.dll','coreclr.dll')) {
    if (!(Test-Path (Join-Path $payload $name))) { throw "Missing published dependency: $name" }
}
Copy-Item (Join-Path $repo 'docs/WINDOWS.md') (Join-Path $payload 'README-Windows.md')
$archive = Join-Path $output "Uoo-Boot-Manager-$version-windows-$cpu.zip"
Compress-Archive -Path "$payload/*" -DestinationPath $archive -CompressionLevel Optimal -Force
if (!$SkipInstaller) {
    if (!$InnoCompiler) { $InnoCompiler = Join-Path ${env:ProgramFiles} 'Inno Setup 7/ISCC.exe' }
    if (!(Test-Path $InnoCompiler)) { throw 'Install Inno Setup 7 or provide -InnoCompiler.' }
    & $InnoCompiler "/DAppVersion=$version" "/DCpuArch=$cpu" "/DPayloadDir=$payload" "/DOutputDir=$output" (Join-Path $repo 'windows/packaging/uoo.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Building the Windows installer failed.' }
}
$packages = @(Get-ChildItem $output -File | Where-Object { $_.Name -match "^Uoo-Boot-Manager-$([regex]::Escape($version))-windows-.*\.(zip|exe)$" } | Sort-Object Name)
$manifest = $packages | ForEach-Object { '{0}  {1}' -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $_.Name }
[IO.File]::WriteAllLines((Join-Path $output 'SHA256SUMS'), [string[]]$manifest, [Text.UTF8Encoding]::new($false))
Write-Output "Built $Runtime version $version in $output"
