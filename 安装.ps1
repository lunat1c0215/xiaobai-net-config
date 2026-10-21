# 安装到当前用户目录，不需要管理员权限
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$dst = Join-Path $env:LOCALAPPDATA 'Programs\XiaobaiBox'

Write-Host "安装到: $dst" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $dst | Out-Null

$self = @('安装.cmd', '安装.ps1', '卸载.cmd', '卸载.ps1')
Get-ChildItem -LiteralPath $src -File | Where-Object { $self -notcontains $_.Name } | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $dst $_.Name) -Force
}

$exe = Join-Path $dst 'XiaobaiBox.exe'
$ver = '2.x'
if (Test-Path -LiteralPath $exe) {
    try {
        $v = (& $exe --version | Out-String).Trim()
        if ($v -match 'XiaobaiBox\s+(\S+)') { $ver = $Matches[1] }
    } catch { }
}

$target = Join-Path $dst '启动小白配网工具.vbs'
$ws = New-Object -ComObject WScript.Shell
function New-Lnk([string]$path) {
    $l = $ws.CreateShortcut($path)
    $l.TargetPath = "$env:SystemRoot\System32\wscript.exe"
    $l.Arguments = '"' + $target + '"'
    $l.WorkingDirectory = $dst
    $l.IconLocation = "$env:SystemRoot\System32\shell32.dll,167"
    $l.Description = '小白盒子配网 + 打印机端口同步'
    $l.Save()
}

$desktop = [Environment]::GetFolderPath('Desktop')
New-Lnk (Join-Path $desktop '小白盒子配网工具.lnk')

$sm = Join-Path ([Environment]::GetFolderPath('Programs')) '小白盒子配网工具'
New-Item -ItemType Directory -Force -Path $sm | Out-Null
New-Lnk (Join-Path $sm '小白盒子配网工具.lnk')

$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\XiaobaiBox'
New-Item -Path $key -Force | Out-Null
Set-ItemProperty -Path $key -Name DisplayName     -Value '小白盒子配网工具'
Set-ItemProperty -Path $key -Name DisplayVersion  -Value $ver
Set-ItemProperty -Path $key -Name Publisher       -Value 'lunat1c0215'
Set-ItemProperty -Path $key -Name InstallLocation -Value $dst
Set-ItemProperty -Path $key -Name DisplayIcon     -Value $exe
Set-ItemProperty -Path $key -Name NoModify        -Value 1 -Type DWord
Set-ItemProperty -Path $key -Name NoRepair        -Value 1 -Type DWord
Set-ItemProperty -Path $key -Name UninstallString `
    -Value ('powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $dst '卸载.ps1') + '"')

Write-Host "安装完成，版本 $ver" -ForegroundColor Green
Write-Host "桌面和开始菜单已创建快捷方式。" -ForegroundColor Green
Write-Host "可在「设置 → 应用」里卸载。" -ForegroundColor Gray

Start-Process -FilePath "$env:SystemRoot\System32\wscript.exe" -ArgumentList ('"' + $target + '"')
