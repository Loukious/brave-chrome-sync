param([Parameter(Mandatory=$true)][string]$Helper)
$ErrorActionPreference = 'Stop'
$testRoot = Join-Path (Split-Path $PSScriptRoot) ('work/taskbar-pin-test-' + [guid]::NewGuid().ToString('N'))
$pinRoot = Join-Path $testRoot 'fake taskbar'
New-Item -ItemType Directory -Path $pinRoot | Out-Null
Copy-Item -LiteralPath $Helper -Destination (Join-Path $testRoot 'SyncBrowser.exe')
@{ repository='Loukious/brave-chrome-sync'; channel='release'; protocol=1 } | ConvertTo-Json | Set-Content (Join-Path $testRoot 'installation.json')
$browserRoot = Join-Path $testRoot 'versions/current'
New-Item -ItemType Directory -Path $browserRoot | Out-Null
$currentBrowser = Join-Path $browserRoot 'brave.exe'
Set-Content -LiteralPath $currentBrowser 'fixture browser'
@{ tag='v1.99.8-sync.r1.0123456789ab'; browser='versions/current/brave.exe'; published_at='2026-10-01T00:00:00Z' } | ConvertTo-Json | Set-Content (Join-Path $testRoot 'current.json')
$shell = New-Object -ComObject WScript.Shell
$linkPath = Join-Path $pinRoot 'Our Browser.lnk'
$link = $shell.CreateShortcut($linkPath)
$link.TargetPath = Join-Path $testRoot 'versions/old/brave.exe'
$expectedArguments = '--user-data-dir="D:\profile with spaces" --profile-directory="Profile 2"'
$link.Arguments = $expectedArguments
$link.IconLocation = (Join-Path $testRoot 'versions/old/brave.exe') + ',0'
$link.Save()
$foreignPath = Join-Path $pinRoot 'Foreign Browser.lnk'
$foreign = $shell.CreateShortcut($foreignPath)
$foreign.TargetPath = Join-Path $env:SystemRoot 'notepad.exe'
$foreign.Save()
$before = (Get-FileHash -LiteralPath $foreignPath).Hash
$process = Start-Process -FilePath (Join-Path $testRoot 'SyncBrowser.exe') -ArgumentList @('--repair-taskbar', '--check', "--root=`"$testRoot`"", "--taskbar-directory=`"$pinRoot`"") -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(30000) -or $process.ExitCode -ne 0) { throw 'Pin repair helper failed.' }
$updated = $shell.CreateShortcut($linkPath)
if ($updated.TargetPath -ne (Join-Path $testRoot 'SyncBrowser.exe') -or $updated.Arguments -ne $expectedArguments -or $updated.WorkingDirectory -ne $testRoot) { throw 'Pin repair damaged target, arguments or working directory.' }
if ($updated.IconLocation -ne $currentBrowser + ',0') { throw 'Shortcut still references an obsolete icon.' }
if ((Get-FileHash -LiteralPath $foreignPath).Hash -ne $before) { throw 'Unrelated shortcut was modified.' }
$explorer = New-Object -ComObject Shell.Application
$appId = $explorer.NameSpace($pinRoot).ParseName('Our Browser.lnk').ExtendedProperty('System.AppUserModel.ID')
if ($appId -ne 'Loukious.BraveChromeSync.release') { throw "Pin identity is incorrect: $appId" }
if (Test-Path (Join-Path $testRoot 'taskbar-warning.txt')) { throw 'Pin repair reported a warning.' }
Write-Host 'Trimmed helper repairs old-version pins, preserves profile arguments and leaves unrelated shortcuts unchanged.'
