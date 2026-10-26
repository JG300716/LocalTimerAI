[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = 'Stop'
$localFlowStartup = [Environment]::GetFolderPath('Startup')
if ([string]::IsNullOrWhiteSpace($localFlowStartup)) {
    throw 'The current user Startup folder is unavailable.'
}
$localFlowShortcutPath = Join-Path $localFlowStartup 'LocalFlow.lnk'
if (Test-Path -LiteralPath $localFlowShortcutPath -PathType Leaf) {
    if ($PSCmdlet.ShouldProcess($localFlowShortcutPath, 'Disable LocalFlow at user sign-in')) {
        Remove-Item -LiteralPath $localFlowShortcutPath
        Write-Host 'LocalFlow autostart disabled.'
    }
} else {
    Write-Host 'LocalFlow autostart was already disabled.'
}
