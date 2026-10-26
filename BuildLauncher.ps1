[CmdletBinding()]
param(
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
$localFlowFolder = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $localFlowFolder 'LocalFlow.exe'
} elseif (-not [IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $localFlowFolder $OutputPath
}
$localFlowOutput = [IO.Path]::GetFullPath($OutputPath)
if ([IO.Path]::GetExtension($localFlowOutput) -ne '.exe') {
    throw 'OutputPath must name an .exe file.'
}
$localFlowOutputFolder = Split-Path -Parent $localFlowOutput
if (-not (Test-Path -LiteralPath $localFlowOutputFolder -PathType Container)) {
    New-Item -ItemType Directory -Path $localFlowOutputFolder -Force | Out-Null
}
$localFlowCompiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $localFlowCompiler -PathType Leaf)) {
    $localFlowCompiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path -LiteralPath $localFlowCompiler -PathType Leaf)) {
    throw '.NET Framework 4.x C# compiler is unavailable. Start.cmd remains usable.'
}
& $localFlowCompiler /nologo /target:winexe /platform:anycpu /optimize+ /codepage:65001 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll ('/out:' + $localFlowOutput) (Join-Path $localFlowFolder 'Launcher.cs')
if ($LASTEXITCODE -ne 0) { throw "Launcher compilation failed: $LASTEXITCODE" }
Write-Host ('Built: ' + $localFlowOutput)
