[CmdletBinding()]
param(
    [string]$Version,
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$releaseRoot = Split-Path -Parent $PSScriptRoot
$releaseSource = Get-Content -LiteralPath (Join-Path $releaseRoot 'app.py') -Raw
$releaseVersionMatch = [regex]::Match($releaseSource, '(?m)^VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?)"\s*$')
if (-not $releaseVersionMatch.Success) { throw 'Cannot read VERSION from app.py.' }
$sourceVersion = $releaseVersionMatch.Groups[1].Value
if ([string]::IsNullOrWhiteSpace($Version)) { $Version = $sourceVersion }
if ($Version -ne $sourceVersion) { throw "Requested version $Version differs from app.py VERSION $sourceVersion." }
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $releaseRoot 'dist'
} elseif (-not [IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory = Join-Path $releaseRoot $OutputDirectory
}
$releaseOutput = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $releaseOutput -Force | Out-Null
$releaseId = [Guid]::NewGuid().ToString('N')
$releaseStage = Join-Path $releaseOutput ('.stage-' + $releaseId)
$releaseTemporaryZip = Join-Path $releaseOutput ('.package-' + $releaseId + '.zip')
$releaseFilename = 'LocalTimerAI-' + $Version + '-windows-x64.zip'
$releaseZip = Join-Path $releaseOutput $releaseFilename

# Deliberate allowlist. Never copy the checkout recursively: config.json,
# data/, logs, backups and generated previews stay local. BuffedTestVoid.vbs
# is the portable repository example, never the user's Desktop copy.
$releaseFiles = @(
    'app.py', 'tracker.py', 'integrations.py',
    'Launcher.cs', 'BuildLauncher.ps1',
    'Start.cmd', 'Stop.cmd', 'StartHidden.ps1', 'StartHidden.vbs',
    'BuffedTestVoid.vbs',
    'EnableAutostart.ps1', 'DisableAutostart.ps1',
    'README.md', 'INTEGRATIONS.md', 'TESTING.md',
    'web/index.html', 'web/app.js', 'web/styles.css'
)

try {
    New-Item -ItemType Directory -Path $releaseStage | Out-Null
    foreach ($releaseFile in $releaseFiles) {
        $releaseSourcePath = Join-Path $releaseRoot $releaseFile
        if (-not (Test-Path -LiteralPath $releaseSourcePath -PathType Leaf)) {
            throw "Missing release input: $releaseFile"
        }
        $releaseDestination = Join-Path $releaseStage $releaseFile
        $releaseDestinationFolder = Split-Path -Parent $releaseDestination
        New-Item -ItemType Directory -Path $releaseDestinationFolder -Force | Out-Null
        Copy-Item -LiteralPath $releaseSourcePath -Destination $releaseDestination
    }
    $releaseExample = Join-Path $releaseRoot 'config.example.json'
    if (-not (Test-Path -LiteralPath $releaseExample -PathType Leaf)) {
        throw 'Missing clean config.example.json; user config.json must never be packaged.'
    }
    Get-Content -LiteralPath $releaseExample -Raw | ConvertFrom-Json | Out-Null
    Copy-Item -LiteralPath $releaseExample -Destination (Join-Path $releaseStage 'config.json')
    & (Join-Path $releaseRoot 'BuildLauncher.ps1') -OutputPath (Join-Path $releaseStage 'LocalFlow.exe')
    $releaseNotice = @"
LocalTimerAI $Version for Windows x64

The application currently uses the LocalFlow executable and local data format.
Requirements: Windows x64, .NET Framework 4.x, Python 3.10 or newer.
Python must be available through py -3 or python on PATH. No pip packages are needed.
This archive does not bundle Python and is not a self-contained executable.

Extract every file to a writable folder. Start LocalFlow.exe for the taskbar
window and local dashboard. Edit config.json to configure tracked applications.
Start.cmd can start the Python service directly. Exit in the LocalFlow window
stops the service. Activity data and local API keys are created in data/ on first run.
No user history, account keys, local configuration or desktop-specific script
is included in this archive. See README.md and INTEGRATIONS.md for details.
"@
    [IO.File]::WriteAllText((Join-Path $releaseStage 'RELEASE.txt'), $releaseNotice, (New-Object Text.UTF8Encoding($false)))
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory($releaseStage, $releaseTemporaryZip)
    Move-Item -LiteralPath $releaseTemporaryZip -Destination $releaseZip -Force
    $releaseHash = (Get-FileHash -LiteralPath $releaseZip -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText((Join-Path $releaseOutput 'SHA256SUMS'), ($releaseHash + '  ' + $releaseFilename + "`n"), (New-Object Text.UTF8Encoding($false)))
    Write-Host ('Created: ' + $releaseZip)
    Write-Host ('SHA256: ' + $releaseHash)
} finally {
    # Verify the exact resolved staging directory before any recursive removal.
    $releaseResolvedStage = [IO.Path]::GetFullPath($releaseStage)
    $releaseOutputPrefix = $releaseOutput.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $releaseResolvedStage.StartsWith($releaseOutputPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($releaseResolvedStage) -ne ('.stage-' + $releaseId)) {
        throw 'Refusing to remove a staging directory outside the release output folder.'
    }
    if (Test-Path -LiteralPath $releaseResolvedStage -PathType Container) {
        Remove-Item -LiteralPath $releaseResolvedStage -Recurse -Force
    }
    if (Test-Path -LiteralPath $releaseTemporaryZip -PathType Leaf) {
        Remove-Item -LiteralPath $releaseTemporaryZip -Force
    }
}
