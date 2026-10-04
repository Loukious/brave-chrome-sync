param([Parameter(Mandatory=$true)][string]$InstallRoot, [string]$Channel='release')
$ErrorActionPreference = 'Stop'
$name = if ($Channel -eq 'release') { 'Brave Chrome Sync' } elseif ($Channel -eq 'beta') { 'Brave Chrome Sync Beta' } else { 'Brave Chrome Sync Nightly' }
$client = "Software\Clients\StartMenuInternet\BraveChromeSync-$Channel"
$capabilities = (Get-Item 'HKCU:\Software\RegisteredApplications').GetValue($name)
if ($capabilities -ne "$client\Capabilities") { throw 'Browser is missing from RegisteredApplications.' }
$details = Get-Item "HKCU:\$capabilities"
if ($details.GetValue('ApplicationName') -ne $name -or -not $details.GetValue('ApplicationDescription') -or $details.GetValue('Hidden') -eq 1) { throw 'Default Apps display information is incomplete.' }
$launcher = Join-Path $InstallRoot 'SyncBrowser.exe'
$expectedCommand = '"' + $launcher + '" "%1"'
foreach ($protocol in @('http','https')) {
    $progId = (Get-Item "HKCU:\$capabilities\URLAssociations").GetValue($protocol)
    if ($progId -ne "BraveChromeSync.$Channel.URL") { throw 'Wrong browser protocol association.' }
    $command = (Get-Item "HKCU:\Software\Classes\$progId\shell\open\command").GetValue('')
    if ($command -ne $expectedCommand) { throw 'Protocol handler bypasses the stable launcher or loses argument quoting.' }
}
foreach ($extension in @('.html','.htm','.pdf')) {
    if ((Get-Item "HKCU:\$capabilities\FileAssociations").GetValue($extension) -ne "BraveChromeSync.$Channel.HTML") { throw 'Missing file association.' }
}
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class SyncAssociationQuery {
    [DllImport("shlwapi.dll", CharSet=CharSet.Unicode)]
    public static extern int AssocQueryString(uint flags, uint kind, string association, string extra, StringBuilder output, ref uint size);
}
'@
foreach ($kind in @('HTML','URL')) {
    $buffer = [Text.StringBuilder]::new(32768)
    $size = [uint32]$buffer.Capacity
    $result = [SyncAssociationQuery]::AssocQueryString(0, 2, "BraveChromeSync.$Channel.$kind", 'open', $buffer, [ref]$size)
    if ($result -ne 0 -or $buffer.ToString() -ne $launcher) { throw 'Windows association API did not resolve the stable launcher.' }
}
Write-Host 'Windows discovers the browser capabilities and resolves HTML and URL handlers to the stable launcher.'
