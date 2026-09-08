$ErrorActionPreference = 'Stop'
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$tools = Join-Path $base 'tools'
New-Item -ItemType Directory -Force -Path $tools | Out-Null

$headers = @{ 'User-Agent' = 'AnamorphicDNGBatch/1.0.3 (Windows; ExifTool bootstrapper)' }
$versionUrls = @(
    'https://exiftool.org/ver.txt',
    'https://exiftool.sourceforge.net/ver.txt'
)

$ver = $null
$versionErrors = @()
foreach ($versionUrl in $versionUrls) {
    try {
        $candidate = (Invoke-WebRequest -UseBasicParsing -Headers $headers $versionUrl).Content.Trim()
        if ($candidate -match '^\d+(\.\d+)+$') {
            $ver = $candidate
            break
        }
        $versionErrors += "$versionUrl returned unexpected data: $candidate"
    } catch {
        $versionErrors += "$versionUrl : $($_.Exception.Message)"
    }
}
if (-not $ver) {
    throw "Could not determine the current ExifTool version.`n$($versionErrors -join "`n")"
}

$fileName = "exiftool-$($ver)_64.zip"
$downloadUrls = @(
    "https://download.sourceforge.net/project/exiftool/files/$fileName",
    "https://sourceforge.net/projects/exiftool/files/$fileName/download"
)

$tmp = Join-Path $env:TEMP ("anamorphic_dng_" + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
try {
    $zip = Join-Path $tmp 'exiftool.zip'
    $downloaded = $false
    $downloadErrors = @()
    foreach ($url in $downloadUrls) {
        try {
            Write-Host "Downloading ExifTool $ver from $url"
            Invoke-WebRequest -UseBasicParsing -Headers $headers $url -OutFile $zip
            # Expand-Archive below also validates that the response is actually a ZIP.
            $downloaded = $true
            break
        } catch {
            Remove-Item $zip -Force -ErrorAction SilentlyContinue
            $downloadErrors += "$url : $($_.Exception.Message)"
        }
    }
    if (-not $downloaded) {
        throw "Could not download ExifTool from the official SourceForge mirrors.`n$($downloadErrors -join "`n")"
    }

    $unpacked = Join-Path $tmp 'unpacked'
    Expand-Archive -Path $zip -DestinationPath $unpacked -Force
    $exe = Get-ChildItem -Path $unpacked -Recurse -File | Where-Object { $_.Name -in @('exiftool(-k).exe','exiftool.exe') } | Select-Object -First 1
    if (-not $exe) { throw 'ExifTool executable not found in downloaded archive.' }
    $files = Get-ChildItem -Path $unpacked -Recurse -Directory -Filter 'exiftool_files' | Select-Object -First 1
    if (-not $files) { throw 'Required exiftool_files folder not found in downloaded archive.' }

    $targetExe = Join-Path $tools 'exiftool.exe'
    $targetFiles = Join-Path $tools 'exiftool_files'
    $stagedExe = Join-Path $tools 'exiftool.exe.new'
    $stagedFiles = Join-Path $tools 'exiftool_files.new'

    Remove-Item $stagedExe -Force -ErrorAction SilentlyContinue
    Remove-Item $stagedFiles -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item $exe.FullName $stagedExe -Force
    Copy-Item $files.FullName $stagedFiles -Recurse -Force

    Remove-Item $targetExe -Force -ErrorAction SilentlyContinue
    Remove-Item $targetFiles -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item $stagedExe $targetExe -Force
    Move-Item $stagedFiles $targetFiles -Force

    Write-Host "Installed ExifTool $ver: $targetExe"
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
Read-Host 'Press Enter to close'
