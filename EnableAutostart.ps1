[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = 'Stop'
$localFlowFolder = Split-Path -Parent $MyInvocation.MyCommand.Path
$localFlowTarget = Join-Path $localFlowFolder 'StartHidden.ps1'
$localFlowTray = Join-Path $localFlowFolder 'LocalFlow.exe'
if (Test-Path -LiteralPath $localFlowTray -PathType Leaf) {
    $localFlowTarget = $localFlowTray
}
if (-not (Test-Path -LiteralPath $localFlowTarget -PathType Leaf)) {
    throw "Missing launcher: $localFlowTarget"
}
$localFlowStartup = [Environment]::GetFolderPath('Startup')
if ([string]::IsNullOrWhiteSpace($localFlowStartup)) {
    throw 'The current user Startup folder is unavailable.'
}
$localFlowShortcutPath = Join-Path $localFlowStartup 'LocalFlow.lnk'
if ($PSCmdlet.ShouldProcess($localFlowShortcutPath, 'Enable LocalFlow at user sign-in')) {
    $localFlowShell = New-Object -ComObject WScript.Shell
    $localFlowShortcut = $localFlowShell.CreateShortcut($localFlowShortcutPath)
    if ($localFlowTarget -eq $localFlowTray) {
        $localFlowShortcut.TargetPath = $localFlowTarget
        $localFlowShortcut.Arguments = '--no-browser'
    } else {
        $localFlowShortcut.TargetPath = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $localFlowShortcut.Arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $localFlowTarget + '"'
    }
    $localFlowShortcut.WorkingDirectory = $localFlowFolder
    $localFlowShortcut.Description = 'LocalFlow local work-time tracker'
    $localFlowShortcut.Save()
    Write-Host "Autostart enabled: $localFlowShortcutPath"
}
