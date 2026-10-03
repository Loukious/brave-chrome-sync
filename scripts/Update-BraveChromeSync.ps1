# Requires PowerShell 7. Install side by side; retain previous versions and profiles.
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')]
    [string]$Repository = 'Loukious/brave-chrome-sync',
    [ValidateSet('nightly', 'beta', 'release')]
    [string]$Channel = 'nightly',
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'BraveChromeSync'),
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'PowerShell 7 is required.' }
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$headers = @{ Accept = 'application/vnd.github+json'; 'User-Agent' = 'BraveChromeSyncUpdater' }
$releases = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repository/releases?per_page=100" -Headers $headers
$release = $releases | Where-Object {
    -not $_.draft -and $_.name -match "^Brave Chrome Sync \[$Channel\] " -and
    $_.tag_name -match '^v\d+\.\d+\.\d+-sync\.r\d+\.[a-f0-9]{12}$'
} | Sort-Object @{ Expression = { [version](($_.tag_name -split '-')[0].Substring(1)) }; Descending = $true },
    @{ Expression = { [int]([regex]::Match($_.tag_name, '-sync\.r(\d+)').Groups[1].Value) }; Descending = $true },
    @{ Expression = { [datetime]$_.published_at }; Descending = $true } | Select-Object -First 1
if (-not $release) { throw "No completed $Channel browser release is available yet." }
$currentFile = Join-Path $InstallRoot 'current.json'
$current = if (Test-Path -LiteralPath $currentFile) { Get-Content -LiteralPath $currentFile -Raw | ConvertFrom-Json } else { $null }
if ($current -and $current.tag -eq $release.tag_name -and $current.repository -eq $Repository) {
    Write-Host "Already current: $($release.tag_name)"
    return
}
Write-Host "Available: $($release.tag_name)"
if ($CheckOnly) { return }
New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
$lockPath = Join-Path $InstallRoot 'update.lock'
$lock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
try {
    $current = if (Test-Path -LiteralPath $currentFile) { Get-Content -LiteralPath $currentFile -Raw | ConvertFrom-Json } else { $null }
    if ($current -and $current.tag -eq $release.tag_name -and $current.repository -eq $Repository) { return }
    # Refuse profile downgrades even when a release is removed upstream.
    if ($current) {
        if ($current.repository -ne $Repository -or $current.channel -ne $Channel) {
            throw 'Use a separate InstallRoot when changing repository or channel.'
        }
        $currentVersion = [version](($current.tag -split '-')[0].Substring(1))
        $nextVersion = [version](($release.tag_name -split '-')[0].Substring(1))
        if ($nextVersion -lt $currentVersion) { throw 'Refusing an automatic browser downgrade.' }
        $installedPrefix = Join-Path $InstallRoot 'versions'
        $running = @(Get-Process -Name brave, chrome -ErrorAction SilentlyContinue | Where-Object {
            $_.Path -and $_.Path.StartsWith($installedPrefix + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
        })
        if ($running.Count -gt 0) {
            Write-Host 'Browser is running; update deferred until the next launch after closing it.'
            return
        }
    }
    $assetName = "brave-chrome-sync-$($release.tag_name)-windows-x64.zip"
    $asset = @($release.assets | Where-Object name -eq $assetName)
    if ($asset.Count -ne 1 -or $asset[0].digest -notmatch '^sha256:[a-fA-F0-9]{64}$') {
        throw 'Release must have exactly one browser ZIP with a GitHub SHA-256 asset digest.'
    }
    $uri = [uri]$asset[0].browser_download_url
    if ($uri.Scheme -ne 'https' -or $uri.Host -ne 'github.com' -or
        -not $uri.AbsolutePath.StartsWith("/$Repository/releases/download/", [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Unexpected release download URL.'
    }
    $staging = Join-Path $InstallRoot ('.staging-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $staging | Out-Null
    $archive = Join-Path $staging 'browser.zip'
    Invoke-WebRequest -Uri $uri.AbsoluteUri -OutFile $archive
    $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ("sha256:$actual" -ne $asset[0].digest.ToLowerInvariant()) { throw 'Browser archive SHA-256 mismatch.' }
    $expanded = Join-Path $staging 'app'
    New-Item -ItemType Directory -Path $expanded | Out-Null
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        $prefix = [IO.Path]::GetFullPath($expanded) + [IO.Path]::DirectorySeparatorChar
        foreach ($entry in $zip.Entries) {
            $entryPath = [IO.Path]::GetFullPath((Join-Path $expanded $entry.FullName))
            if (-not $entryPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
                $entry.FullName.Contains(':')) { throw 'Unsafe path in browser ZIP.' }
        }
    } finally { $zip.Dispose() }
    [IO.Compression.ZipFile]::ExtractToDirectory($archive, $expanded)
    $binaries = @(Get-ChildItem -LiteralPath $expanded -Recurse -File | Where-Object Name -in @('brave.exe', 'chrome.exe'))
    if ($binaries.Count -ne 1) { throw 'Expected exactly one browser executable in archive.' }
    $relativeExe = [IO.Path]::GetRelativePath($expanded, $binaries[0].FullName)
    $versionRoot = Join-Path $InstallRoot ('versions/' + $release.tag_name + '-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path (Split-Path $versionRoot) -Force | Out-Null
    Move-Item -LiteralPath $expanded -Destination $versionRoot
    $state = @{ tag = $release.tag_name; repository = $Repository; channel = $Channel;
        executable = [IO.Path]::GetRelativePath($InstallRoot, (Join-Path $versionRoot $relativeExe));
        sha256 = $actual;
        previous = $(if ($current) { @{ tag = $current.tag; executable = $current.executable } } else { $null });
        checked_at = [datetime]::UtcNow.ToString('o') }
    $temporaryState = Join-Path $InstallRoot 'current.json.new'
    $state | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $temporaryState -Encoding utf8
    [IO.File]::Move($temporaryState, $currentFile, $true)
    Write-Host "Installed $($release.tag_name). Previous binaries and user data were retained."
} finally {
    $lock.Dispose()
}
