[CmdletBinding()]
param(
    [ValidateSet('win-x64','win-arm64')][string]$Runtime = 'win-x64',
    [string]$InnoCompiler = '',
    [switch]$SkipInstaller
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Cross-publishing the Windows payload works on Linux; Inno Setup needs a
# Windows host. Preserve explicit switch values and the Windows CI default.
if (!$PSBoundParameters.ContainsKey('SkipInstaller')) { $SkipInstaller = !$IsWindows }
if (!$SkipInstaller) {
    if (!$IsWindows) { throw 'Windows installer creation requires a Windows host. Use -SkipInstaller to build the Windows ZIP on this host.' }
    if (!$InnoCompiler) {
        if (!$env:ProgramFiles) { throw 'ProgramFiles is unavailable. Install Inno Setup 7 and provide -InnoCompiler with the path to ISCC.exe.' }
        $InnoCompiler = Join-Path $env:ProgramFiles 'Inno Setup 7/ISCC.exe'
    }
    if (!(Test-Path -LiteralPath $InnoCompiler -PathType Leaf)) { throw 'Install Inno Setup 7 or provide -InnoCompiler. Use -SkipInstaller for a ZIP-only build.' }
} elseif (!$IsWindows) {
    Write-Output 'Non-Windows host: building the Windows ZIP and checksums. Installer creation requires Windows.'
}
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
Copy-Item (Join-Path $repo 'LICENSE') (Join-Path $payload 'LICENSE')
Copy-Item (Join-Path $repo 'windows/Uoo.Desktop/uoo.png') (Join-Path $payload 'uoo.png')
$guide = (Get-Content (Join-Path $repo 'docs/WINDOWS.md') -Raw).Replace('../assets/icons/png/uoo-64.png', 'uoo.png')
[IO.File]::WriteAllText((Join-Path $payload 'README-Windows.md'), $guide, [Text.UTF8Encoding]::new($false))
$archive = Join-Path $output "Uoo-Boot-Manager-$version-windows-$cpu.zip"
Compress-Archive -Path "$payload/*" -DestinationPath $archive -CompressionLevel Optimal -Force
if (!$SkipInstaller) {
    & $InnoCompiler "/DAppVersion=$version" "/DCpuArch=$cpu" "/DPayloadDir=$payload" "/DOutputDir=$output" (Join-Path $repo 'windows/packaging/uoo.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Building the Windows installer failed.' }
}
$packages = @(Get-ChildItem $output -File | Where-Object { $_.Name -match "^Uoo-Boot-Manager-$([regex]::Escape($version))-windows-.*\.(zip|exe)$" } | Sort-Object Name)
$manifest = $packages | ForEach-Object { '{0}  {1}' -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $_.Name }
[IO.File]::WriteAllLines((Join-Path $output 'SHA256SUMS'), [string[]]$manifest, [Text.UTF8Encoding]::new($false))
$formats = if ($SkipInstaller) { 'ZIP and checksums' } else { 'ZIP, installer, and checksums' }
Write-Output "Built $Runtime version $version ($formats) in $output"
