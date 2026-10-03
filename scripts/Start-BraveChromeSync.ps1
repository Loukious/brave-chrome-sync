[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'BraveChromeSync'),
    [ValidateSet('nightly', 'beta', 'release')]
    [string]$Channel = 'nightly',
    [switch]$SkipUpdate,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$BrowserArguments
)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'PowerShell 7 is required.' }
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$stateFile = Join-Path $InstallRoot 'current.json'
$repository = 'Loukious/brave-chrome-sync'
if (Test-Path -LiteralPath $stateFile) {
    $installed = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    $repository = $installed.repository
    if (-not $PSBoundParameters.ContainsKey('Channel')) { $Channel = $installed.channel }
}
# Check on launch; network failure may fall back only to an installed build.
if (-not $SkipUpdate) {
    try { & (Join-Path $PSScriptRoot 'Update-BraveChromeSync.ps1') -Repository $repository -InstallRoot $InstallRoot -Channel $Channel }
    catch {
        if (-not (Test-Path -LiteralPath $stateFile)) { throw }
        Write-Warning "Update check failed; starting installed build. $($_.Exception.Message)"
    }
}
$state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
$exe = [IO.Path]::GetFullPath((Join-Path $InstallRoot $state.executable))
$prefix = [IO.Path]::GetFullPath((Join-Path $InstallRoot 'versions')) + [IO.Path]::DirectorySeparatorChar
if (-not $exe.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $exe)) {
    throw 'Installed browser path is invalid.'
}
$profile = Join-Path $InstallRoot 'User Data'
$start = [Diagnostics.ProcessStartInfo]::new($exe)
$start.UseShellExecute = $false
$start.ArgumentList.Add("--user-data-dir=$profile")
foreach ($argument in $BrowserArguments) { $start.ArgumentList.Add($argument) }
[Diagnostics.Process]::Start($start) | Out-Null
