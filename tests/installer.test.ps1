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
$initialLauncher = Start-Process -FilePath $launcher -ArgumentList @("--fixture-args=`"$capture`"", "--user-data-dir=`"$profile`"", 'https://example.test/a?x=one&y=two') -WindowStyle Hidden -PassThru
if (-not $initialLauncher.WaitForExit(30000) -or $initialLauncher.ExitCode -ne 0) { throw 'Initial launcher failed.' }
$deadline = [datetime]::UtcNow.AddSeconds(30)
while (-not (Test-Path $capture) -and [datetime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 200 }
$arguments = Get-Content $capture -Raw | ConvertFrom-Json
if ($arguments -contains '--root' -or $arguments -notcontains "--user-data-dir=$profile" -or $arguments -notcontains 'https://example.test/a?x=one&y=two') { throw 'Launcher damaged browser arguments.' }
if ((Get-Content (Join-Path $profile 'sentinel.txt') -Raw).Trim() -ne 'profile remains') { throw 'Profile changed during installation or launch.' }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Programs')) 'Brave Chrome Sync/Brave Chrome Sync.lnk'))
if ($shortcut.TargetPath -ne $launcher) { throw 'Start Menu shortcut does not use the stable launcher.' }

$defaultCapture = Join-Path $root 'default-profile-arguments.json'
$isolatedLocal = Join-Path $root 'isolated-local-appdata'
$previousLocal = $env:LOCALAPPDATA
try {
    $env:LOCALAPPDATA = $isolatedLocal
    $defaultLauncher = Start-Process -FilePath $launcher -ArgumentList @("--fixture-args=`"$defaultCapture`"") -WindowStyle Hidden -PassThru
    if (-not $defaultLauncher.WaitForExit(30000) -or $defaultLauncher.ExitCode -ne 0) { throw 'Default profile launcher failed.' }
} finally { $env:LOCALAPPDATA = $previousLocal }
$deadline = [datetime]::UtcNow.AddSeconds(30)
while (-not (Test-Path $defaultCapture) -and [datetime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 200 }
$defaultArguments = Get-Content $defaultCapture -Raw | ConvertFrom-Json
if ($defaultArguments -notcontains "--user-data-dir=$(Join-Path $isolatedLocal 'BraveChromeSync/User Data')") { throw 'Launcher did not select the shared default profile.' }

# Exercise the same parent-wait/activation route used by the native Relaunch hook.
$heldCapture = Join-Path $root 'held-browser.json'
$pidFile = Join-Path $root 'held-browser.pid'
$releaseParent = Join-Path $root 'release-parent.txt'
# Start-Process -Wait waits for descendants, including the browser we hold open.
$heldLauncher = Start-Process -FilePath $launcher -ArgumentList @("--fixture-args=`"$heldCapture`"", "--fixture-pid=`"$pidFile`"", "--fixture-wait=`"$releaseParent`"", "--user-data-dir=`"$profile`"") -WindowStyle Hidden -PassThru
if (-not $heldLauncher.WaitForExit(30000) -or $heldLauncher.ExitCode -ne 0) { throw 'Held browser launcher failed.' }
$deadline = [datetime]::UtcNow.AddSeconds(30)
while (-not (Test-Path $pidFile) -and [datetime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 200 }
$parentPid = [int](Get-Content $pidFile -Raw)
$newTag = 'v1.99.9-sync.r2.abcdef012345'
$newFolder = Join-Path $root "versions/$newTag"
Copy-Item (Join-Path $root "versions/$tag") $newFolder -Recurse
@{ release_tag = $newTag; channel = 'nightly'; protocol = 1 } | ConvertTo-Json | Set-Content (Join-Path $newFolder 'sync-release.json')
@{ tag = $newTag; browser = "versions/$newTag/brave.exe"; published_at = '2026-10-03T12:00:00Z' } | ConvertTo-Json | Set-Content (Join-Path $root 'pending.json')
$restartCapture = Join-Path $root 'restart-arguments.json'
$helper = Join-Path $root "versions/$tag/SyncUpdater.exe"
if (-not (Get-Process -Id $parentPid -ErrorAction SilentlyContinue)) { throw 'Fixture parent exited before the relaunch test began.' }
$restart = Start-Process -FilePath $helper -ArgumentList @('--relaunch', "--parent-pid=$parentPid", '--', "--fixture-args=`"$restartCapture`"", "--user-data-dir=`"$profile`"", '--restore-last-session') -WindowStyle Hidden -PassThru
Start-Sleep -Milliseconds 500
if (-not (Get-Process -Id $parentPid -ErrorAction SilentlyContinue)) { throw 'Fixture parent exited before the activation assertion.' }
if ((Get-Content (Join-Path $root 'current.json') -Raw | ConvertFrom-Json).tag -ne $tag) { throw 'Update activated while parent browser was running.' }
Set-Content $releaseParent 'exit'
if (-not $restart.WaitForExit(30000) -or $restart.ExitCode -ne 0) { throw 'Relaunch helper failed.' }
$deadline = [datetime]::UtcNow.AddSeconds(30)
while (-not (Test-Path $restartCapture) -and [datetime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 200 }
$restartArguments = Get-Content $restartCapture -Raw | ConvertFrom-Json
if ((Get-Content (Join-Path $root 'current.json') -Raw | ConvertFrom-Json).tag -ne $newTag -or $restartArguments -notcontains '--restore-last-session') { throw 'Relaunch did not activate the staged version and restore session arguments.' }
if ((Get-Content (Join-Path $root 'previous.json') -Raw | ConvertFrom-Json).tag -ne $tag -or (Get-Content (Join-Path $profile 'sentinel.txt') -Raw).Trim() -ne 'profile remains') { throw 'Relaunch lost the previous version or profile.' }
Write-Host 'Installer, shortcuts, argument preservation, parent-exit wait, staged relaunch and profile preservation passed.'
& (Join-Path $PSScriptRoot 'taskbar-pins.test.ps1') -Helper $helper
