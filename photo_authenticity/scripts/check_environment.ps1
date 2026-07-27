param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExe
)

$ErrorActionPreference = "Stop"
$resolvedPython = (Resolve-Path -LiteralPath $PythonExe).Path
$versionText = & $resolvedPython -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to query Python version."
}

$version = [version]$versionText.Trim()
if ($version.Major -ne 3 -or $version.Minor -ne 11) {
    throw "Python 3.11 is required; found $versionText."
}

Write-Output $versionText
