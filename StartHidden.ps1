[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$localFlowFolder = Split-Path -Parent $MyInvocation.MyCommand.Path
$localFlowTarget = Join-Path $localFlowFolder 'Start.cmd'
if (-not (Test-Path -LiteralPath $localFlowTarget -PathType Leaf)) {
    throw "Missing launcher: $localFlowTarget"
}
# Reuse Start.cmd's tested Python fallback, including broken Store aliases.
$localFlowArguments = '/d /s /c ""' + $localFlowTarget + '" --no-browser"'
Start-Process -FilePath $env:ComSpec -ArgumentList $localFlowArguments -WorkingDirectory $localFlowFolder -WindowStyle Hidden
