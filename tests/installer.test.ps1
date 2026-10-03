$ErrorActionPreference = 'Stop'
if (-not $env:GITHUB_ACTIONS) { throw 'Installer smoke test requires an ephemeral GitHub runner.' }
$root = Join-Path $env:RUNNER_TEMP ('installer-smoke-' + [guid]::NewGuid().ToString('N'))
$tag = 'v1.99.8-sync.r2.0123456789ab'
$setup = Join-Path $PSScriptRoot "../work/installer-fixture-dist/brave-chrome-sync-$tag-windows-x64-setup.exe"
$process = Start-Process -FilePath $setup -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS', "/DIR=`"$root`"") -WindowStyle Hidden -PassThru -Wait
if ($process.ExitCode -ne 0) { throw "Installer failed: $($process.ExitCode)" }
$current = Get-Content (Join-Path $root 'current.json') -Raw | ConvertFrom-Json
if ($current.tag -ne $tag) { throw 'Installer did not select its release.' }
$profile = Join-Path $root 'Test User Data'
New-Item $profile -ItemType Directory | Out-Null
Set-Content (Join-Path $profile 'sentinel.txt') 'profile remains'
$capture = Join-Path $root 'arguments with spaces.json'
$launcher = Join-Path $root 'SyncBrowser.exe'
Start-Process -FilePath $launcher -ArgumentList @("--fixture-args=`"$capture`"", "--user-data-dir=`"$profile`"", 'https://example.test/a?x=one&y=two') -WindowStyle Hidden -Wait
$deadline = [datetime]::UtcNow.AddSeconds(30)
while (-not (Test-Path $capture) -and [datetime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 200 }
$arguments = Get-Content $capture -Raw | ConvertFrom-Json
if ($arguments -contains '--root' -or $arguments -notcontains "--user-data-dir=$profile" -or $arguments -notcontains 'https://example.test/a?x=one&y=two') { throw 'Launcher damaged browser arguments.' }
if ((Get-Content (Join-Path $profile 'sentinel.txt') -Raw).Trim() -ne 'profile remains') { throw 'Profile changed during installation or launch.' }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Programs')) 'Brave Chrome Sync/Brave Chrome Sync.lnk'))
if ($shortcut.TargetPath -ne $launcher) { throw 'Start Menu shortcut does not use the stable launcher.' }
Write-Host 'Installer initialized the release; stable launcher preserved arguments and profile; Start Menu shortcut verified.'
