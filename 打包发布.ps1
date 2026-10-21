# 构建可分发包：dist_new\XiaobaiBox.exe + GUI + 启动器 -> 发布\*.zip
param([string]$Version = '2.0.1')
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
foreach ($f in @('xiaobai_gui.ps1', '启动小白配网工具.vbs', '修复打印队列.ps1',
                 '发布说明.txt', '使用说明.html')) {
    if (Test-Path -LiteralPath $f) { Copy-Item $f $out -Force }
}
foreach ($f in @('xiaobai_gui.ps1', '修复打印队列.ps1', '发布说明.txt')) {
    $p = Join-Path $out $f
    if (Test-Path -LiteralPath $p) { Save-Utf8Bom $p }
}

$zip = Join-Path $root "小白盒子配网工具-$Version.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path (Join-Path $out '*') -DestinationPath $zip -CompressionLevel Optimal
Write-Host "[+] 完成: $out"
Write-Host "[+] 压缩包: $zip"
