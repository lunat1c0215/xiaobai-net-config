# 修复打印队列：清空卡死的作业 + 重启打印后台服务
# 用法：右键 → 使用 PowerShell 运行（会自动请求管理员权限）

$ErrorActionPreference = 'Continue'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "需要管理员权限，正在请求提权 ..." -ForegroundColor Yellow
    try {
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit',
            '-File', $PSCommandPath
        )
    } catch {
        Write-Host "提权被取消。请手动：右键本文件 → 以管理员身份运行" -ForegroundColor Red
    }
    exit
}

Write-Host "=== 1. 停止打印后台服务 ===" -ForegroundColor Cyan
Stop-Service -Name Spooler -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Write-Host ("  Spooler = " + (Get-Service Spooler).Status)

Write-Host "=== 2. 清空卡死的打印作业文件 ===" -ForegroundColor Cyan
$dir = 'C:\Windows\System32\spool\PRINTERS'
$left = Get-ChildItem -LiteralPath $dir -File -Force -ErrorAction SilentlyContinue
if ($left) {
    $left | ForEach-Object { Write-Host "  删除 $($_.Name)" }
    $left | Remove-Item -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "  目录已空"
}
$still = Get-ChildItem -LiteralPath $dir -File -Force -ErrorAction SilentlyContinue
if ($still) {
    Write-Host "  [!] 仍有文件删不掉：" -ForegroundColor Yellow
    $still | ForEach-Object { Write-Host "      $($_.Name)" -ForegroundColor Yellow }
} else {
    Write-Host "  ✓ 全部清理完毕" -ForegroundColor Green
}

Write-Host "=== 3. 启动打印后台服务 ===" -ForegroundColor Cyan
Start-Service -Name Spooler
Start-Sleep -Seconds 2
Write-Host ("  Spooler = " + (Get-Service Spooler).Status)

Write-Host "=== 4. 检查队列 ===" -ForegroundColor Cyan
Get-Printer | Select-Object Name, PortName, PrinterStatus, JobCount | Format-Table -AutoSize
foreach ($p in (Get-Printer)) {
    $jobs = Get-PrintJob -PrinterName $p.Name -ErrorAction SilentlyContinue
    if ($jobs) { Write-Host "  [!] $($p.Name) 还有 $($jobs.Count) 个作业未清" -ForegroundColor Yellow }
}
Write-Host "`n完成。现在可以重试打印了。" -ForegroundColor Green
