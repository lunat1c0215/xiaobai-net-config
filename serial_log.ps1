# serial_log.ps1 - USB-TTL 串口抓取 / 交互 (用于抓盒子 U-Boot 和 Linux 启动日志)
# 用法:
#   .\serial_log.ps1 -ListPorts
#   .\serial_log.ps1 -Port COM5 -Baud 115200 -Out .\box_boot.log
#   .\serial_log.ps1 -Port COM5 -Baud 115200 -Out .\box_boot.log -SendOnStart "`r" -AutoEnter
#
# 接线(3.3V TTL, 千万别接 5V):
#   盒子 GND <-> 模块 GND
#   盒子 TX  <-> 模块 RX
#   盒子 RX  <-> 模块 TX
#   盒子 VCC  不要接 (盒子自己供电)

param(
  [switch]$ListPorts,
  [string]$Port = "",
  [int]$Baud = 115200,
  [string]$Out = "",
  [string]$SendOnStart = "",
  [switch]$AutoEnter,        # 每秒发一次回车, 用于抢占 U-Boot
  [int]$Seconds = 0          # 0 = 一直抓, 直到 Ctrl+C
)

$ErrorActionPreference = 'Continue'

if ($ListPorts -or -not $Port) {
  Write-Host "=== 可用串口 ==="
  [System.IO.Ports.SerialPort]::GetPortNames() | ForEach-Object { Write-Host "  $_" }
  Write-Host ""
  Write-Host "=== 串口设备详情 ==="
  Get-CimInstance Win32_PnPEntity |
    Where-Object { $_.Name -match '\(COM\d+\)' } |
    Select-Object Name, DeviceID | Format-Table -AutoSize -Wrap
  if (-not $Port) { Write-Host "`n用 -Port COMx 指定端口后重跑。"; exit 0 }
}

if (-not $Out) { $Out = Join-Path (Get-Location) ("serial_{0}_{1}.log" -f $Port, (Get-Date -Format 'yyyyMMdd_HHmmss')) }

Write-Host "[*] 打开 $Port @ $Baud 8N1  ->  日志: $Out"
$sp = New-Object System.IO.Ports.SerialPort $Port, $Baud, 'None', 8, 'One'
$sp.ReadTimeout = 500
$sp.WriteTimeout = 500
$sp.DtrEnable = $true
$sp.RtsEnable = $true

try { $sp.Open() } catch { Write-Host "[!] 打开失败: $($_.Exception.Message)"; exit 1 }

$sw = [System.IO.StreamWriter]::new($Out, $true, [System.Text.Encoding]::UTF8)
$sw.AutoFlush = $true
$sw.WriteLine("# serial log  port=$Port baud=$Baud  start=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")

if ($SendOnStart) { try { $sp.Write($SendOnStart); Write-Host "[>] 已发送初始字符" } catch {} }

$deadline = if ($Seconds -gt 0) { (Get-Date).AddSeconds($Seconds) } else { [DateTime]::MaxValue }
$lastEnter = Get-Date

Write-Host "[*] 抓取中... 现在给盒子上电。(Ctrl+C 退出)"
try {
  while ((Get-Date) -lt $deadline) {
    if ($AutoEnter -and ((Get-Date) - $lastEnter).TotalMilliseconds -ge 1000) {
      try { $sp.Write("`r") } catch {}
      $lastEnter = Get-Date
    }
    try {
      $chunk = $sp.ReadExisting()
      if ($chunk -and $chunk.Length -gt 0) {
        Write-Host -NoNewline $chunk
        $sw.Write($chunk)
      }
    } catch { }
    Start-Sleep -Milliseconds 60
  }
} finally {
  $sw.WriteLine("# end=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
  $sw.Close()
  try { $sp.Close() } catch {}
  Write-Host "`n[*] 已保存: $Out"
}
