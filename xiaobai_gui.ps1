# 小白盒子配网工具 - Windows 原生窗口 (WinForms)
# 依赖: 同目录下的 ble_config.py (python + bleak)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Security
[System.Windows.Forms.Application]::EnableVisualStyles()

# 高分屏下不做位图拉伸，避免界面发糊（Win10 1703+）
try {
    Add-Type -Namespace Win32 -Name DpiHelper -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
'@
    [void][Win32.DpiHelper]::SetProcessDpiAwarenessContext([IntPtr]-4)
} catch { }

$script:Dir      = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$script:PyScript = Join-Path $script:Dir 'xiaobai_box.py'
$script:JsonOut  = Join-Path $env:TEMP 'xb_ble_result.json'
$script:LogOut   = Join-Path $env:TEMP 'xb_ble_log.txt'
$script:HistFile = Join-Path $env:LOCALAPPDATA 'XiaobaiBox\wifi_history.json'
$script:Ps       = $null
$script:Async    = $null
$script:Running  = $false
$script:Mode     = ''
$script:Hist     = @()
$script:LastSsid = ''
$script:LastPwd  = ''
$script:Mark     = ' ✓'
$script:Engine   = $null

# 分发时优先用同目录自带的 exe；只有开发机才回退到 python
function Resolve-Engine {
    foreach ($sub in @('', 'dist_new', '发布')) {
        $exe = if ($sub) { Join-Path $script:Dir "$sub\XiaobaiBox.exe" } else { Join-Path $script:Dir 'XiaobaiBox.exe' }
        if (Test-Path -LiteralPath $exe) {
            return @{ Path = $exe; Pre = @() }
        }
    }
    $cands = @()
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { $cands += $cmd.Source }
    $cands += @(
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe",
        "$env:ProgramFiles\Python312\python.exe")
    foreach ($p in $cands) {
        if (-not (Test-Path -LiteralPath $p)) { continue }
        try {
            $v = & $p --version 2>&1
            if ($LASTEXITCODE -eq 0 -and "$v" -match 'Python 3') {
                if (-not (Test-Path -LiteralPath $script:PyScript)) { continue }
                return @{ Path = $p; Pre = @($script:PyScript) }
            }
        } catch {
            continue
        }
    }
    return $null
}

$script:Engine = Resolve-Engine
$null = New-Item -ItemType Directory -Force -Path (Split-Path $script:HistFile) -ErrorAction SilentlyContinue
# 旧版本把记忆放在程序目录，迁移一次
$script:OldHist = Join-Path $script:Dir 'wifi_history.json'
if (-not (Test-Path -LiteralPath $script:HistFile) -and (Test-Path -LiteralPath $script:OldHist)) {
    try { Copy-Item -LiteralPath $script:OldHist -Destination $script:HistFile -Force } catch { }
}

if (-not $script:Engine) {
    [System.Windows.Forms.MessageBox]::Show(
        "找不到运行引擎。`n`n请把 XiaobaiBox.exe 放在本工具同一目录，`n或安装 Python 3.11+ 后重试。",
        '小白盒子配网工具')
    exit 1
}

function Quote([string]$s) { "'" + ($s -replace "'", "''") + "'" }

# ---------- Wi-Fi 记忆 (DPAPI 加密, 仅当前用户可解) ----------
function Protect-Str([string]$s) {
    if ([string]::IsNullOrEmpty($s)) { return '' }   # 开放网络：空密码
    $b = [System.Text.Encoding]::UTF8.GetBytes($s)
    $p = [System.Security.Cryptography.ProtectedData]::Protect($b, $null, 'CurrentUser')
    return [Convert]::ToBase64String($p)
}
function Unprotect-Str([string]$b64) {
    try {
        $p = [Convert]::FromBase64String($b64)
        $b = [System.Security.Cryptography.ProtectedData]::Unprotect($p, $null, 'CurrentUser')
        return [System.Text.Encoding]::UTF8.GetString($b)
    } catch { return '' }
}
function Save-Hist {
    try { $script:Hist | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $script:HistFile -Encoding UTF8 } catch { }
}
function Load-Hist {
    $script:Hist = @()
    if (Test-Path -LiteralPath $script:HistFile) {
        try {
            $raw = Get-Content -LiteralPath $script:HistFile -Raw -Encoding UTF8
            if ($raw -and $raw.Trim()) {
                $o = $raw | ConvertFrom-Json
                if ($o) { $script:Hist = @($o) }
            }
        } catch { $script:Hist = @() }
    }
}
function Get-HistPwd([string]$ssid) {
    $h = $script:Hist | Where-Object { $_.ssid -eq $ssid } | Select-Object -First 1
    if (-not $h) { return '' }
    return (Unprotect-Str $h.pwd)
}
function Get-HistEntry([string]$ssid) {
    return ($script:Hist | Where-Object { $_.ssid -eq $ssid } | Select-Object -First 1)
}
function Add-Hist([string]$ssid, [string]$pwd) {
    $script:Hist = @($script:Hist | Where-Object { $_.ssid -ne $ssid })
    $entry = [pscustomobject]@{
        ssid = $ssid
        pwd  = (Protect-Str $pwd)
        last = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    }
    $script:Hist = @($entry) + @($script:Hist | Select-Object -First 19)
    Save-Hist
}
# 列表项里存的是 "SSID ✓"，取出真实 SSID
function Raw-Ssid([string]$item) {
    return ($item -replace '\s+✓$', '')
}

# ---------------- UI ----------------
$form = New-Object System.Windows.Forms.Form
$form.Text          = '小白盒子配网工具'
$form.ClientSize    = New-Object System.Drawing.Size(620, 642)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedSingle'
$form.MaximizeBox   = $false
$form.Font          = New-Object System.Drawing.Font('Microsoft YaHei UI', 9)
$form.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::Dpi
$form.AutoScaleDimensions = New-Object System.Drawing.SizeF(96, 96)

$btnScan = New-Object System.Windows.Forms.Button
$btnScan.Text = '扫描盒子'
$btnScan.Location = New-Object System.Drawing.Point(12, 12)
$btnScan.Size = New-Object System.Drawing.Size(110, 30)

$lblBox = New-Object System.Windows.Forms.Label
$lblBox.Text = '未扫描'
$lblBox.Location = New-Object System.Drawing.Point(134, 19)
$lblBox.Size = New-Object System.Drawing.Size(470, 18)
$lblBox.ForeColor = [System.Drawing.Color]::DimGray

$lblList = New-Object System.Windows.Forms.Label
$lblList.Text = '盒子扫到的 Wi-Fi（选一个；带 ✓ 的已记住密码）'
$lblList.Location = New-Object System.Drawing.Point(12, 50)
$lblList.Size = New-Object System.Drawing.Size(400, 18)

$lst = New-Object System.Windows.Forms.ListBox
$lst.Location = New-Object System.Drawing.Point(12, 70)
$lst.Size = New-Object System.Drawing.Size(592, 180)
$lst.IntegralHeight = $false

$lblPwd = New-Object System.Windows.Forms.Label
$lblPwd.Text = 'Wi-Fi 密码'
$lblPwd.Location = New-Object System.Drawing.Point(12, 266)
$lblPwd.Size = New-Object System.Drawing.Size(80, 20)

$txtPwd = New-Object System.Windows.Forms.TextBox
$txtPwd.Location = New-Object System.Drawing.Point(100, 262)
$txtPwd.Size = New-Object System.Drawing.Size(348, 25)
$txtPwd.UseSystemPasswordChar = $true

$chkOpen = New-Object System.Windows.Forms.CheckBox
$chkOpen.Text = '开放网络（无密码）'
$chkOpen.Location = New-Object System.Drawing.Point(458, 264)
$chkOpen.Size = New-Object System.Drawing.Size(150, 22)

$btnCfg = New-Object System.Windows.Forms.Button
$btnCfg.Text = '配网 + 同步打印机'
$btnCfg.Location = New-Object System.Drawing.Point(12, 298)
$btnCfg.Size = New-Object System.Drawing.Size(160, 34)

$btnSync = New-Object System.Windows.Forms.Button
$btnSync.Text = '仅同步打印机端口'
$btnSync.Location = New-Object System.Drawing.Point(180, 298)
$btnSync.Size = New-Object System.Drawing.Size(150, 34)

$btnDoctor = New-Object System.Windows.Forms.Button
$btnDoctor.Text = '体检（打不出来）'
$btnDoctor.Location = New-Object System.Drawing.Point(338, 298)
$btnDoctor.Size = New-Object System.Drawing.Size(140, 34)

$btnHelp = New-Object System.Windows.Forms.Button
$btnHelp.Text = '使用说明'
$btnHelp.Location = New-Object System.Drawing.Point(486, 298)
$btnHelp.Size = New-Object System.Drawing.Size(122, 34)

$btnDiag = New-Object System.Windows.Forms.Button
$btnDiag.Text = '导出诊断包'
$btnDiag.Location = New-Object System.Drawing.Point(12, 346)
$btnDiag.Size = New-Object System.Drawing.Size(110, 26)

$lblVersion = New-Object System.Windows.Forms.Label
$lblVersion.Text = 'v2.x'
$lblVersion.Location = New-Object System.Drawing.Point(500, 352)
$lblVersion.Size = New-Object System.Drawing.Size(108, 18)
$lblVersion.TextAlign = 'MiddleRight'
$lblVersion.ForeColor = [System.Drawing.Color]::Gray

$lblQueue = New-Object System.Windows.Forms.Label
$lblQueue.Text = 'Word 里选：自动识别中…'
$lblQueue.Location = New-Object System.Drawing.Point(12, 382)
$lblQueue.Size = New-Object System.Drawing.Size(592, 18)
$lblQueue.ForeColor = [System.Drawing.Color]::SteelBlue

$lblProg = New-Object System.Windows.Forms.Label
$lblProg.Text = '进度'
$lblProg.Location = New-Object System.Drawing.Point(12, 414)
$lblProg.Size = New-Object System.Drawing.Size(60, 18)

$prog = New-Object System.Windows.Forms.ProgressBar
$prog.Location = New-Object System.Drawing.Point(60, 412)
$prog.Size = New-Object System.Drawing.Size(544, 20)
$prog.Minimum = 0
$prog.Maximum = 100

$lblLog = New-Object System.Windows.Forms.Label
$lblLog.Text = '日志'
$lblLog.Location = New-Object System.Drawing.Point(12, 440)
$lblLog.Size = New-Object System.Drawing.Size(80, 18)

$txtLog = New-Object System.Windows.Forms.TextBox
$txtLog.Location = New-Object System.Drawing.Point(12, 460)
$txtLog.Size = New-Object System.Drawing.Size(592, 155)
$txtLog.Multiline = $true
$txtLog.ReadOnly = $true
$txtLog.ScrollBars = 'Vertical'
$txtLog.BackColor = [System.Drawing.Color]::White
$txtLog.Font = New-Object System.Drawing.Font('Consolas', 8.5)

$status = New-Object System.Windows.Forms.StatusStrip
$statusLabel = New-Object System.Windows.Forms.ToolStripStatusLabel
$statusLabel.Text = '就绪'
$status.Items.Add($statusLabel) | Out-Null

$form.Controls.AddRange(@($btnScan, $lblBox, $lblList, $lst, $lblPwd, $txtPwd,
                          $chkOpen, $btnCfg, $btnSync, $btnDoctor, $btnHelp,
                          $btnDiag, $lblVersion, $lblQueue,
                          $lblProg, $prog, $lblLog, $txtLog, $status))

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 400

function Set-Status([string]$t) { $script:statusLabel.Text = $t }
function Update-QueueLabel([string[]]$names) {
    if ($names -and @($names).Count -gt 0) {
        $script:lblQueue.Text = 'Word 里请选：' + (@($names) -join '  /  ')
    } else {
        $script:lblQueue.Text = 'Word 里请选：未识别到队列（用「体检」查看）'
    }
}
function Append-Log([string]$t) {
    if (-not $t) { return }
    $script:txtLog.AppendText(($t.TrimEnd() + "`r`n"))
    $script:txtLog.SelectionStart = $script:txtLog.TextLength
    $script:txtLog.ScrollToCaret()
}
function Set-Busy([bool]$b, [string]$text) {
    $script:btnScan.Enabled = -not $b
    $script:btnCfg.Enabled  = -not $b
    $script:btnSync.Enabled = -not $b
    $script:btnDoctor.Enabled = -not $b
    $script:btnDiag.Enabled = -not $b
    if ($b -and $text) { Set-Status $text }
}

function Get-Progress {
    if (-not (Test-Path -LiteralPath $script:LogOut)) { return $null }
    try { $txt = Get-Content -LiteralPath $script:LogOut -Raw -Encoding UTF8 } catch { return $null }
    if (-not $txt) { return $null }
    $m = [regex]::Matches($txt, '##PROG##(\d+)\|([^\r\n]*)')
    if ($m.Count -eq 0) { return $null }
    $last = $m[$m.Count - 1]
    return @{ pct = [int]$last.Groups[1].Value; text = $last.Groups[2].Value }
}

# 同步跑一条引擎命令（只用于毫秒级的状态查询，不用于扫描）
function Invoke-EngineSync {
    param([string[]]$ArgList)
    $out = Join-Path $env:TEMP 'xb_sync_check.json'
    if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force }
    $call = @($script:Engine.Path) + @($script:Engine.Pre) + @($ArgList) + @('--json-out', $out)
    $argStr = ($call | ForEach-Object { Quote $_ }) -join ' '
    try { Invoke-Expression ("& " + $argStr + " *> `$null") } catch { }
    if (Test-Path -LiteralPath $out) {
        try { return (Get-Content -LiteralPath $out -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { }
    }
    return $null
}

function Start-Task {
    param([string[]]$PyArgs, [string]$Mode, [string]$BusyText)
    if ($script:Running) { return }
    $script:Running = $true
    $script:Mode = $Mode
    $script:prog.Value = 0
    Set-Busy $true $BusyText
    if (Test-Path -LiteralPath $script:JsonOut) { Remove-Item -LiteralPath $script:JsonOut -Force }
    if (Test-Path -LiteralPath $script:LogOut)  { Remove-Item -LiteralPath $script:LogOut  -Force }
    $call = @($script:Engine.Path) + @($script:Engine.Pre) + @($PyArgs) +
            @('--json-out', $script:JsonOut, '--log-out', $script:LogOut)
    $argStr = ($call | ForEach-Object { Quote $_ }) -join ' '
    $envLine = if ($Mode -eq 'set+sync' -and $script:LastPwd) {
        "`$env:XB_WIFI_PWD = " + (Quote $script:LastPwd)
    } else { '' }
    $cmd = @"
`$env:PYTHONIOENCODING='utf-8'
`$env:PYTHONUTF8='1'
$envLine
Set-Location -LiteralPath $(Quote $script:Dir)
Invoke-Expression $(Quote ("& " + $argStr + " *>&1 | Out-Null"))
"@
    $script:Ps = [PowerShell]::Create()
    [void]$script:Ps.AddScript($cmd)
    $script:Async = $script:Ps.BeginInvoke()
    $script:timer.Start()
}

function Handle-Output([string]$text) {
    $logTxt = ''
    if (Test-Path -LiteralPath $script:LogOut) {
        try { $logTxt = Get-Content -LiteralPath $script:LogOut -Raw -Encoding UTF8 } catch { }
    }
    if (-not $logTxt) { $logTxt = $text }
    foreach ($line in ($logTxt -split "`r?`n")) {
        if ($line -match '^###JSON###') { continue }
        if ($line -match '^##PROG##')   { continue }
        if ($line.Trim()) { Append-Log $line }
    }

    $j = $null
    if (Test-Path -LiteralPath $script:JsonOut) {
        try { $j = Get-Content -LiteralPath $script:JsonOut -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
    }
    if (-not $j) { Set-Status '未拿到结果，看日志'; return }

    if ($j.error -eq 'bluetooth_off') {
        Set-Status '电脑蓝牙未打开（已尝试自动开启失败）'
        [System.Windows.Forms.MessageBox]::Show(
            "电脑蓝牙没有打开，程序已经尝试自动开启，但没有成功。`n`n" +
            "接下来会自动打开 Windows 蓝牙设置，请把「蓝牙」开关打开后重试。",
            '需要打开蓝牙', [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Warning) | Out-Null
        try { Start-Process 'ms-settings:bluetooth' } catch { }
        return
    }
    if ($j.error -eq 'bluetooth_absent') {
        Set-Status '这台电脑没有蓝牙适配器'
        [System.Windows.Forms.MessageBox]::Show(
            "系统里没有找到蓝牙适配器。`n`n台式机常见：需要插一个 USB 蓝牙适配器。",
            '没有蓝牙硬件', [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
        return
    }

    switch ($script:Mode) {
        'scan' {
            if ($j.ok) {
                $script:lst.Items.Clear()
                $known = @($script:Hist | ForEach-Object { $_.ssid })
                $hitN = 0
                foreach ($s in @($j.ssids)) {
                    if ($known -contains $s) { [void]$script:lst.Items.Add($s + $script:Mark); $hitN++ }
                    else { [void]$script:lst.Items.Add($s) }
                }
                $script:lblBox.Text = "盒子: $($j.name)   BLE: $($j.addr)"
                $script:prog.Value = 100
                Set-Status "扫描完成：共 $(@($j.ssids).Count) 个，其中 $hitN 个已记住密码"
            } else {
                $script:prog.Value = 0
                if ($j.error -eq 'bluetooth_off') {
                    Set-Status '电脑蓝牙未打开（已尝试自动开启失败）'
                    [System.Windows.Forms.MessageBox]::Show(
                        "电脑蓝牙没有打开，程序已经尝试自动开启，但没有成功。`n`n" +
                        "接下来会自动打开 Windows 蓝牙设置，请把「蓝牙」开关打开，再回来点「扫描盒子」。",
                        '需要打开蓝牙', [System.Windows.Forms.MessageBoxButtons]::OK,
                        [System.Windows.Forms.MessageBoxIcon]::Warning) | Out-Null
                    try { Start-Process 'ms-settings:bluetooth' } catch { }
                } elseif ($j.error -eq 'bluetooth_absent') {
                    Set-Status '这台电脑没有蓝牙适配器'
                    [System.Windows.Forms.MessageBox]::Show(
                        "系统里没有找到蓝牙适配器。`n`n台式机常见：需要插一个 USB 蓝牙适配器。",
                        '没有蓝牙硬件', [System.Windows.Forms.MessageBoxButtons]::OK,
                        [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
                } else {
                    Set-Status '没扫到盒子'
                    [System.Windows.Forms.MessageBox]::Show(
                        "没扫到盒子。请确认：`n  · 盒子已通电、距离较近`n  · 电脑蓝牙已打开`n  · 手机上的小白 App 没有占用蓝牙",
                        '扫描失败') | Out-Null
                }
            }
        }
        'set' {
            if ($j.ok) {
                if ($script:LastSsid) { Add-Hist $script:LastSsid $script:LastPwd }
                Set-Status "配网指令已下发（$($j.sent) 段）；盒子联网状态 = $($j.state)"
            } else { Set-Status '配网失败（记忆未更新）' }
        }
        'set+sync' {
            if ($j.ok) {
                if ($script:LastSsid) { Add-Hist $script:LastSsid $script:LastPwd }
                $net = if ($j.net_up) { '盒子已联网' } else { '盒子状态未确认' }
                Set-Status "配网成功（$net），打印机端口已指向 $($j.ip)；已更新记忆"
            } else {
                if ($j.error -in @('ble_connect_failed','ble_disconnected','ble_subscribe_failed','ble_timeout')) {
                    Set-Status '蓝牙通信失败，配网未完成'
                    [System.Windows.Forms.MessageBox]::Show(
                        "配网没有完成：`n$($j.detail)`n`n$($j.hint)",
                        '配网失败', [System.Windows.Forms.MessageBoxButtons]::OK,
                        [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
                    return
                }
                $st = if ($null -ne $j.net_state) { "$($j.net_state)" } else { '未知' }
                if ($j.ble_alive) {
                    Set-Status "配网失败（盒子联网状态=$st）；蓝牙还在线，可直接再配一次"
                    $msg = "配网失败：盒子没有连上新网络（联网状态上报 = $st）。`n`n常见原因：密码错误 / 这个 Wi-Fi 盒子连不上。`n`n好消息：盒子蓝牙仍在线，你可以直接再配一次，不用碰盒子。"
                } else {
                    Set-Status "配网失败（盒子联网状态=$st）；蓝牙也离线了，需要断电重启"
                    $msg = "配网失败（联网状态 = $st），而且盒子的蓝牙也探测不到了。`n`n请把盒子断电重启，等它重新广播后，再点「扫描盒子」重来。"
                }
                $ans = [System.Windows.Forms.MessageBox]::Show(
                    $msg + "`n`n是否仍然记住这次输入的密码？（未验证，可能不正确）",
                    '配网失败', [System.Windows.Forms.MessageBoxButtons]::YesNo,
                    [System.Windows.Forms.MessageBoxIcon]::Error)
                if ($ans -eq [System.Windows.Forms.DialogResult]::Yes -and $script:LastSsid) {
                    Add-Hist $script:LastSsid $script:LastPwd
                    Set-Status '已按你的要求记住密码（未验证）'
                } else {
                    Set-Status '配网失败，记忆未更新'
                }
            }
        }
        'sync' {
            if ($j.ok) {
                Set-Status "打印机端口已指向 $($j.ip)"
                if ($j.queues) { Update-QueueLabel $j.queues }
            }
            else {
                $why = if ($j.results -and @($j.results)[0].error) { @($j.results)[0].error }
                       elseif ($j.hint) { $j.hint } else { $j.error }
                Set-Status "同步失败：$why"
            }
        }
        'queues' {
            if ($j.queues) { Update-QueueLabel $j.queues }
            Set-Status "已识别 $(@($j.queues).Count) 个打印队列"
        }
        'doctor' {
            if ($j.conclusion) { Set-Status $j.conclusion }
            if ($j.queues) { Update-QueueLabel $j.queues }
        }
        'diag' {
            if ($j.ok) {
                Set-Status "诊断包已保存：$($j.path)"
                try { Start-Process explorer.exe -ArgumentList "/select,`"$($j.path)`"" } catch { }
            } else {
                Set-Status "诊断包生成失败：$($j.error)"
            }
        }
    }
}

$timer.Add_Tick({
    if ($null -eq $script:Async) { return }
    if (-not $script:Async.IsCompleted) {
        $p = Get-Progress
        if ($p) {
            if ($p.pct -gt $script:prog.Value) { $script:prog.Value = [Math]::Min(100, $p.pct) }
            if ($p.text) { Set-Status $p.text }
        } elseif ($script:prog.Value -lt 90) {
            $script:prog.Value = [Math]::Min(90, $script:prog.Value + 2)
        }
        return
    }
    $script:timer.Stop()
    $out = ''
    try { $out = ($script:Ps.EndInvoke($script:Async) -join "`r`n") } catch { $out = "ERR: $($_.Exception.Message)" }
    try { $script:Ps.Dispose() } catch { }
    $script:Ps = $null; $script:Async = $null; $script:Running = $false
    Set-Busy $false
    Handle-Output $out
    if ($script:prog.Value -lt 100) { $script:prog.Value = 100 }
})

# ---------------- 事件 ----------------
$btnScan.Add_Click({
    # 扫描前先查蓝牙：关着就提醒，用户同意才自动打开
    $oldCursor = $form.Cursor
    try { $form.Cursor = 'WaitCursor' } catch { }
    $bt = Invoke-EngineSync @('--bt-status', '--no-auto-on', '--json')
    try { $form.Cursor = $oldCursor } catch { }

    if ($bt -and $bt.state -eq 'absent') {
        Set-Status '这台电脑没有蓝牙适配器'
        [System.Windows.Forms.MessageBox]::Show(
            "系统里没有找到蓝牙适配器。`n`n台式机常见：需要插一个 USB 蓝牙适配器。",
            '没有蓝牙硬件', [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
        return
    }
    if ($bt -and $bt.state -eq 'off') {
        $r = [System.Windows.Forms.MessageBox]::Show(
            "电脑蓝牙现在是关着的。`n`n" +
            "点「是」：程序自动打开蓝牙，然后开始扫描。`n" +
            "点「否」：打开 Windows 蓝牙设置，你自己打开。",
            '蓝牙未打开', [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Question)
        if ($r -ne [System.Windows.Forms.DialogResult]::Yes) {
            try { Start-Process 'ms-settings:bluetooth' } catch { }
            Set-Status '已打开蓝牙设置，请手动打开后重试'
            return
        }
        Set-Status '正在自动打开蓝牙并扫描 ...'
    }
    $script:txtLog.Clear()
    $script:txtPwd.Clear()
    Start-Task -PyArgs @('--list', '--json', '--scan-time', '25') -Mode 'scan' -BusyText '正在扫描蓝牙（约 25 秒）...'
})

$btnSync.Add_Click({
    Start-Task -PyArgs @('--only-sync', '--json') -Mode 'sync' -BusyText '正在同步打印机端口...'
})

$btnDoctor.Add_Click({
    $script:txtLog.Clear()
    Start-Task -PyArgs @('--doctor', '--json') -Mode 'doctor' -BusyText '正在体检打印链路（约 5 秒）...'
})

$btnHelp.Add_Click({
    $doc = Join-Path $script:Dir '使用说明.html'
    if (Test-Path -LiteralPath $doc) {
        Start-Process -FilePath $doc
    } else {
        $txt = Join-Path $script:Dir '发布说明.txt'
        if (Test-Path -LiteralPath $txt) { Start-Process -FilePath $txt; return }
        [System.Windows.Forms.MessageBox]::Show(
            "找不到 使用说明.html。`n`n请确认它和本程序在同一个文件夹里。",
            '使用说明', [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
    }
})

$btnDiag.Add_Click({
    Start-Task -PyArgs @('--diagnostics', '--json') -Mode 'diag' -BusyText '正在生成诊断包（约 5 秒）...'
})

# 选中列表里的 Wi-Fi -> 有记忆就自动填密码
$lst.Add_SelectedIndexChanged({
    if ($script:lst.SelectedIndex -lt 0) { return }
    $ssid = Raw-Ssid ([string]$script:lst.SelectedItem)
    $h = Get-HistEntry $ssid
    if ($h) {
        if ([string]::IsNullOrEmpty($h.pwd)) {
            $script:txtPwd.Clear()
            $script:chkOpen.Checked = $true          # 上次是开放网络
            Set-Status "「$ssid」是开放网络（无密码），已自动勾选"
        } else {
            $script:chkOpen.Checked = $false
            $script:txtPwd.Text = (Unprotect-Str $h.pwd)
            Set-Status "已自动填入密码: $ssid   (上次 $(if ($h.last) { $h.last } else { '—' }))"
        }
    } else {
        $script:txtPwd.Clear()
        Set-Status "「$ssid」没有记忆，请手动输入密码，或勾选「开放网络（无密码）」"
    }
})

$chkOpen.Add_CheckedChanged({
    $script:txtPwd.Enabled = -not $script:chkOpen.Checked
    if ($script:chkOpen.Checked) { $script:txtPwd.Clear() }
})

$btnCfg.Add_Click({
    if ($script:lst.SelectedIndex -lt 0) {
        [System.Windows.Forms.MessageBox]::Show('请先扫描并选中一个 Wi-Fi。', '提示') | Out-Null; return
    }
    $ssid = Raw-Ssid ([string]$script:lst.SelectedItem)
    $open = $script:chkOpen.Checked
    $pwd  = $script:txtPwd.Text
    if (-not $open -and [string]::IsNullOrEmpty($pwd)) {
        [System.Windows.Forms.MessageBox]::Show('请输入 Wi-Fi 密码，或勾选「开放网络（无密码）」。', '提示') | Out-Null; return
    }
    $desc = if ($open) { "$ssid`n（开放网络，无密码）" } else { $ssid }
    $r = [System.Windows.Forms.MessageBox]::Show(
        "将把盒子配到:`n`n    $desc`n`n盒子会断开当前网络去连新的；密码错就要重新配网。继续？",
        '确认配网', [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Warning)
    if ($r -ne [System.Windows.Forms.DialogResult]::Yes) { return }

    $script:LastSsid = $ssid
    $script:LastPwd  = if ($open) { '' } else { $pwd }
    if ($open) {
        Start-Task -PyArgs @('--set-open', $ssid, '--json') -Mode 'set+sync' -BusyText '正在配网（开放网络），等待盒子接入...'
    } else {
        Start-Task -PyArgs @('--set-ssid', $ssid, '--json') -Mode 'set+sync' -BusyText '正在配网，等待盒子接入新网络...'
    }
})

$form.Add_Shown({
    Load-Hist
    try {
        $eargs = @($script:Engine.Pre) + @('--version')
        $v = (& $script:Engine.Path @eargs 2>$null | Out-String).Trim()
        if ($v -match 'XiaobaiBox\s+(\S+)') {
            $script:lblVersion.Text = 'v' + $Matches[1]
            $form.Text = "小白盒子配网工具 v$($Matches[1])"
        }
    } catch { }
    Set-Status "就绪（已记住 $(@($script:Hist).Count) 个 Wi-Fi）"
    Start-Task -PyArgs @('--queues', '--json') -Mode 'queues' -BusyText '正在识别打印队列...'
})
[void]$form.ShowDialog()
$timer.Dispose()
