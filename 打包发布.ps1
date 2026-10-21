# 构建可分发包：dist_new\XiaobaiBox.exe + GUI + 启动器 + 安装脚本 -> 发布\*.zip
# 版本号唯一来源：xiaobai_box.py 里的 __version__（不再手写默认值）
param([string]$Version)
$ErrorActionPreference = 'Stop'
$root = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
Set-Location -LiteralPath $root

function Find-Python {
    $cands = @()
    $c = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($c) { $cands += $c.Source }
    $cands += @(
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe",
        "$env:ProgramFiles\Python312\python.exe")
    foreach ($p in $cands) {
        if (-not (Test-Path -LiteralPath $p)) { continue }
        try {
            $v = & $p --version 2>&1
            if ($LASTEXITCODE -eq 0 -and "$v" -match 'Python 3') { return $p }
        } catch { }
    }
    throw '找不到 Python 3.11+，无法构建引擎'
}

function Save-Utf8Bom([string]$path) {
    $text = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)
    [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding($true)))
}

$py = Find-Python
Write-Host "[*] Python: $py"

if (-not $Version) {
    $src = [IO.File]::ReadAllText((Join-Path $root 'xiaobai_box.py'), [Text.Encoding]::UTF8)
    $m = [regex]::Match($src, '(?m)^__version__\s*=\s*"([^"]+)"')
    if ($m.Success) { $Version = $m.Groups[1].Value }
}
if (-not $Version) { throw '无法从 xiaobai_box.py 读取 __version__' }
Write-Host "[*] 版本: $Version"

Write-Host '[*] 构建引擎 ...'
& $py -m PyInstaller --noconfirm --onefile --name XiaobaiBox `
    --distpath 'dist_new' --workpath 'build_tmp' --specpath 'build_tmp' xiaobai_box.py | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 构建失败' }

$out = [IO.Path]::GetFullPath((Join-Path $root '发布'))
$rootFull = [IO.Path]::GetFullPath($root)
if (-not $out.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
    throw "输出目录越界: $out"
}
if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Recurse -Force }
New-Item -ItemType Directory -Path $out | Out-Null

Copy-Item 'dist_new\XiaobaiBox.exe' $out -Force
$payload = @('xiaobai_gui.ps1', '启动小白配网工具.vbs', '修复打印队列.ps1',
             '发布说明.txt', '使用说明.html',
             '安装.ps1', '安装.cmd', '卸载.ps1', '卸载.cmd',
             'LICENSE', 'CHANGELOG.md', '发布检查单.md')
foreach ($f in $payload) {
    if (Test-Path -LiteralPath $f) { Copy-Item $f $out -Force }
    else { Write-Warning "缺少文件: $f" }
}

# PowerShell 5.1 会把无 BOM 的 UTF-8 当 GBK，所有 .ps1 必须补 BOM
Get-ChildItem -LiteralPath $out -Filter '*.ps1' | ForEach-Object { Save-Utf8Bom $_.FullName }
$p = Join-Path $out '发布说明.txt'
if (Test-Path -LiteralPath $p) { Save-Utf8Bom $p }

# 校验文件：发布目录与仓库根各一份，方便直接挂到 Release
$hashLines = Get-ChildItem -LiteralPath $out -File |
    Where-Object { $_.Name -notlike 'SHA256SUMS-*' } |
    Sort-Object Name |
    ForEach-Object { '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash, $_.Name }
$hashName = "SHA256SUMS-$Version.txt"
$hashText = ($hashLines -join "`r`n") + "`r`n"
[IO.File]::WriteAllText((Join-Path $out $hashName), $hashText, (New-Object Text.UTF8Encoding($false)))
[IO.File]::WriteAllText((Join-Path $root $hashName), $hashText, (New-Object Text.UTF8Encoding($false)))

$zip = Join-Path $root "小白盒子配网工具-$Version.zip"
$zipAscii = Join-Path $root "xiaobai-net-config-$Version.zip"
foreach ($z in @($zip, $zipAscii)) {
    if (Test-Path -LiteralPath $z) { Remove-Item -LiteralPath $z -Force }
}
Compress-Archive -Path (Join-Path $out '*') -DestinationPath $zip -CompressionLevel Optimal
Copy-Item -LiteralPath $zip -Destination $zipAscii -Force

Write-Host "[+] 完成: $out"
Write-Host "[+] 压缩包: $zip"
Write-Host "[+] 上传用: $zipAscii"
Write-Host "[+] 校验:   $(Join-Path $root $hashName)"
Write-Host "[+] SHA256:  $((Get-FileHash -LiteralPath $zipAscii -Algorithm SHA256).Hash)"