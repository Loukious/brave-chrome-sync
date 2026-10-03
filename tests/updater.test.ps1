$ErrorActionPreference = 'Stop'
$testRoot = Join-Path (Split-Path $PSScriptRoot) ('work/updater-tests-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
$global:SyncUpdaterTest = @{
    Fixture = Join-Path $testRoot 'fixture.zip'
    Tag = 'v1.99.9-sync.r1.0123456789ab'
    BadDigest = $false
    Downloads = 0
}

function New-Fixture([string]$EntryName) {
    $stream = [IO.File]::Open($global:SyncUpdaterTest.Fixture, [IO.FileMode]::Create)
    $zip = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Create)
    try {
        $entry = $zip.CreateEntry($EntryName)
        $writer = [IO.StreamWriter]::new($entry.Open())
        $writer.Write('browser fixture; never executed')
        $writer.Dispose()
    } finally { $zip.Dispose(); $stream.Dispose() }
}

function Invoke-RestMethod {
    param($Uri, $Headers)
    if ($Uri -notlike 'https://api.github.com/repos/Loukious/brave-chrome-sync/releases*') { throw 'Unexpected API request' }
    $digest = (Get-FileHash -LiteralPath $global:SyncUpdaterTest.Fixture -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($global:SyncUpdaterTest.BadDigest) { $digest = '0' * 64 }
    @{
        name = "Brave Chrome Sync [nightly] $($global:SyncUpdaterTest.Tag)"
        tag_name = $global:SyncUpdaterTest.Tag
        draft = $false
        published_at = '2026-10-03T00:00:00Z'
        assets = @(@{
            name = "brave-chrome-sync-$($global:SyncUpdaterTest.Tag)-windows-x64.zip"
            digest = "sha256:$digest"
            browser_download_url = "https://github.com/Loukious/brave-chrome-sync/releases/download/$($global:SyncUpdaterTest.Tag)/browser.zip"
        })
    }
}

function Invoke-WebRequest {
    param($Uri, $OutFile)
    $global:SyncUpdaterTest.Downloads++
    Copy-Item -LiteralPath $global:SyncUpdaterTest.Fixture -Destination $OutFile
}

function Assert-Throws([scriptblock]$Action) {
    $failed = $false
    try { & $Action } catch { $failed = $true; Write-Host "Expected failure: $($_.Exception.Message)" }
    if (-not $failed) { throw 'Expected updater to reject the input' }
}

$updater = Join-Path (Split-Path $PSScriptRoot) 'scripts/Update-BraveChromeSync.ps1'
New-Fixture 'brave.exe'
$installed = Join-Path $testRoot 'installed'
& $updater -InstallRoot $installed
$before = Get-Content -LiteralPath (Join-Path $installed 'current.json') -Raw
$state = $before | ConvertFrom-Json
if ($state.tag -ne $global:SyncUpdaterTest.Tag -or -not (Test-Path -LiteralPath (Join-Path $installed $state.executable))) {
    throw 'Valid package was not installed'
}
& $updater -InstallRoot $installed
if ($global:SyncUpdaterTest.Downloads -ne 1) { throw 'An identical installed release was downloaded again' }

$global:SyncUpdaterTest.Tag = 'v1.99.10-sync.r1.0123456789ab'
$global:SyncUpdaterTest.BadDigest = $true
Assert-Throws { & $updater -InstallRoot $installed }
if ((Get-Content -LiteralPath (Join-Path $installed 'current.json') -Raw) -ne $before) { throw 'Digest failure changed the installed browser' }

$global:SyncUpdaterTest.BadDigest = $false
New-Fixture '../escaped.exe'
Assert-Throws { & $updater -InstallRoot (Join-Path $testRoot 'zip-slip') }
if (Test-Path -LiteralPath (Join-Path $testRoot 'escaped.exe')) { throw 'ZIP path escaped install root' }

New-Fixture 'not-a-browser.txt'
Assert-Throws { & $updater -InstallRoot (Join-Path $testRoot 'missing-browser') }

$global:SyncUpdaterTest.Tag = 'v1.99.8-sync.r1.0123456789ab'
New-Fixture 'brave.exe'
Assert-Throws { & $updater -InstallRoot $installed }
if ((Get-Content -LiteralPath (Join-Path $installed 'current.json') -Raw) -ne $before) { throw 'Downgrade changed the installed browser' }
Write-Host 'Updater tests passed: install, repeat, digest mismatch, unsafe ZIP, missing executable, downgrade.'
