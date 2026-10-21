param([switch]$PurgeData)

$ErrorActionPreference = 'Continue'
$dst = Join-Path $env:LOCALAPPDATA 'Programs\XiaobaiBox'

Write-Host "正在卸载小白盒子配网工具 ..." -ForegroundColor Cyan

$lnks = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) '小白盒子配网工具.lnk'),
    (Join-Path (Join-Path ([Environment]::GetFolderPath('Programs')) '小白盒子配网工具') '小白盒子配网工具.lnk')
)
foreach ($l in $lnks) {
    if (Test-Path -LiteralPath $l) {
        Remove-Item -LiteralPath $l -Force -ErrorAction SilentlyContinue
    }
}
$smDir = Join-Path ([Environment]::GetFolderPath('Programs')) '小白盒子配网工具'
if (Test-Path -LiteralPath $smDir) {
    Remove-Item -LiteralPath $smDir -Recurse -Force -ErrorAction SilentlyContinue
}

$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\XiaobaiBox'
if (Test-Path $key) { Remove-Item -Path $key -Recurse -Force -ErrorAction SilentlyContinue }

if ($PurgeData) {
    $data = Join-Path $env:LOCALAPPDATA 'XiaobaiBox'
    if (Test-Path -LiteralPath $data) {
        Remove-Item -LiteralPath $data -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "已清除配置与 Wi-Fi 记忆。" -ForegroundColor Yellow
    }
} else {
    Write-Host "配置与 Wi-Fi 记忆保留在: $env:LOCALAPPDATA\XiaobaiBox" -ForegroundColor Gray
    Write-Host "如需一并删除，运行: 卸载.ps1 -PurgeData" -ForegroundColor Gray
}

Write-Host "程序目录将在 2 秒后删除。" -ForegroundColor Gray
Start-Process -FilePath 'cmd.exe' -WindowStyle Hidden `
    -ArgumentList @('/c', "timeout /t 2 >nul & rmdir /s /q `"$dst`"")
Write-Host "卸载完成。" -ForegroundColor Green
