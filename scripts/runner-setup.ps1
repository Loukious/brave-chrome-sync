$ErrorActionPreference = 'Stop'
Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -Value 1
New-Item -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock' -Force | Out-Null
Set-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock' -Name AllowDevelopmentWithoutDevLicense -Value 1
git config --global core.longpaths true
git config --global core.autocrlf false
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$installation = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $installation) { throw 'Visual Studio C++ toolchain is missing.' }
Write-Host "Visual Studio: $installation"
Get-PSDrive -PSProvider FileSystem | Select-Object Name, Used, Free | Format-Table
if (-not (Test-Path 'C:\Program Files (x86)\Windows Kits\10\Include\10.0.28000.0')) {
    $installer = Join-Path $env:RUNNER_TEMP 'winsdksetup.exe'
    Invoke-WebRequest -Uri 'https://download.microsoft.com/download/38ab8f6d-3676-4860-ae84-3361308b9d7f/KIT_BUNDLE_WINDOWSSDK_MEDIACREATION/winsdksetup.exe' -OutFile $installer
    $process = Start-Process -FilePath $installer -ArgumentList '/features OptionId.DesktopCPPx64 OptionId.WindowsDesktopDebuggers /q /norestart /ceip off' -PassThru -Wait -WindowStyle Hidden
    if ($process.ExitCode -notin @(0, 3010)) { throw "Windows SDK install failed: $($process.ExitCode)" }
}
'DEPOT_TOOLS_WIN_TOOLCHAIN=0' | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
'PYTHONUTF8=1' | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
'SISO_LIMITS=local=2' | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
