# KeymouseGo Download Script
# Downloads the keyboard/mouse automation tool from official GitHub releases

$version = "v5_2_1"
$filename = "KeymouseGo_${version}-win.exe"
$outputDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$outputPath = Join-Path $outputDir $filename

if (Test-Path $outputPath) {
    Write-Host "KeymouseGo already exists: $filename" -ForegroundColor Green
    exit 0
}

Write-Host "Downloading KeymouseGo $version ..." -ForegroundColor Cyan

# Official GitHub release URL
$urls = @(
    "https://github.com/taojy123/KeymouseGo/releases/download/$version/$filename",
    "https://github.com/taojy123/KeymouseGo/releases/download/v5.2.1/$filename"
)

foreach ($url in $urls) {
    try {
        Write-Host "Trying: $url"
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $url -OutFile $outputPath -ErrorAction Stop
        Write-Host "Download successful!" -ForegroundColor Green
        Write-Host "Saved to: $outputPath"
        exit 0
    } catch {
        Write-Host "  Failed: $_" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Automatic download failed." -ForegroundColor Red
Write-Host "Please download manually from:"
Write-Host "  https://github.com/taojy123/KeymouseGo/releases"
Write-Host ""
Write-Host "Save the file as '$filename' in:"
Write-Host "  $outputDir"
Write-Host ""
exit 1
