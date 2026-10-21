# 小白盒子配网 + 打印端口工具（可分发版）

给 XB9 系列小白盒子用：BLE 配网 → 找到盒子 IP → 把 Windows 里**已有的**打印队列端口改到新 IP。
不创建打印机、不装驱动、不碰 Word 设置；Word 里原来选哪台，修完还是那台。

## 下载

[GitHub Releases](https://github.com/lunat1c0215/xiaobai-net-config/releases/latest) → `xiaobai-net-config-2.0.0.zip`

## 分发形态

```
发布\
  XiaobaiBox.exe           引擎（自带 Python + bleak，单文件）
  xiaobai_gui.ps1          WinForms 界面
  启动小白配网工具.vbs      无黑窗口启动器
  修复打印队列.ps1          作业卡死时才用
  发布说明.txt
```

双击 `启动小白配网工具.vbs` 即可。目标机不需要装 Python。

重新打包：

```powershell
powershell -ExecutionPolicy Bypass -File .\打包发布.ps1
```

## 不写死任何本机配置

| 项 | 做法 |
|---|---|
| 盒子 MAC | 从 BLE 名字推导：`XB900WB-C01C303521A3` → `C0:1C:30:35:21:A3` |
| 盒子 BLE 地址 | 每次按名字前缀 `XB9` 重新扫描（地址会变，不缓存死值） |
| 打印机队列 | 按“端口指向盒子 IP”反查，支持一台盒子多个队列 |
| 队列兜底 | 配置里记住的队列 → 同一失效 IP 上的队列组 → 唯一 9100 队列 |
| 配置 | `%LOCALAPPDATA%\XiaobaiBox\config.json` |
| Wi-Fi 记忆 | `%LOCALAPPDATA%\XiaobaiBox\wifi_history.json`（DPAPI，仅当前用户可解） |

## 命令

```powershell
XiaobaiBox.exe --list                      # 读盒子扫到的 Wi-Fi
XiaobaiBox.exe --set "SSID" "密码"          # 配网 + 找 IP + 修端口
XiaobaiBox.exe --set-open "SSID"           # 开放网络
XiaobaiBox.exe --sync                      # 只修端口（换 IP 后最常用）
XiaobaiBox.exe --doctor                    # 结论式体检
XiaobaiBox.exe --queues                    # 看自动认出的队列
XiaobaiBox.exe --watch                     # 守护：IP 变化自动修端口
```

常用开关：

- `--queue NAME`：手动指定队列（可多次），默认自动反查
- `--ip IP`：跳过扫描，直接指定盒子 IP
- `--dry-run`：只显示计划，不改系统
- `--force`：不等待作业，强制切端口（默认会等作业打完）
- `--json` / `--json-out FILE` / `--log-out FILE`：给 GUI 用的机器可读输出

## 改端口的实现

`PrintManagement` 没有 `Set-PrinterPort`，所以：

1. 等 `JobCount == 0`（默认最多 120 秒，超时就跳过，不硬切）
2. `Add-PrinterPort` 建 `IP_<新IP>`（已存在则跳过）
3. `Set-Printer -PortName` 指过去
4. 删除没有任何队列在用的 `IP_*` 旧端口（不碰 USB / 本地端口）
5. 非管理员时弹一次 UAC，只对这段脚本提权

## 蓝牙预检（不是等异常冒出来）

扫描前先读 Windows 无线电状态（`Windows.Devices.Radios`）：

| 状态 | 处理 |
|---|---|
| `on` | 直接扫描 |
| `off` | GUI 先弹窗询问；同意后调 `SetStateAsync(ON)`，轮询最多 6 秒 |
| 自动打开失败 | 返回 `error=bluetooth_off`，GUI 弹提示并打开 `ms-settings:bluetooth` |
| `absent` | 明确告知“这台电脑没有蓝牙适配器” |
| 没有 winrt | 退化为真扫 1.5 秒，从 `BleakBluetoothNotAvailableError` 归类 |

`XiaobaiBox.exe --bt-status` 可单独检查（默认会尝试打开）。

## 体检输出

```
[OK] Spooler 服务 / 本机已联网 / 找到打印队列
[X ] 盒子在线   -> 盒子不在本网段：重新配网，或把电脑切过去
[X ] 9100 可达  -> 盒子没开机 / USB 没插好
[X ] 无卡死作业 -> 管理员运行 修复打印队列.ps1
结论: ...
```

## 错误码（GUI 只看这个，不解析英文）

| code | 含义 | 处理 |
|---|---|---|
| `bluetooth_off` | 蓝牙关着且自动打开失败 | 打开设置页让用户开 |
| `bluetooth_absent` | 没有蓝牙适配器 | 提示插 USB 适配器 |
| `ble_connect_failed` | 连不上盒子 | 靠近、关手机 App，重试 |
| `ble_disconnected` | 下发途中断开 | 盒子重启属正常；等 10 秒再扫 |
| `ble_timeout` | 等列表/等 true 超时 | 断电重启盒子 |
| `no_password` | 环境变量里没密码 | 重新输入 |
| `bad_ip` | `--ip` 不是合法地址 | 修正参数 |
| `spooler_stopped` | 打印服务停了 | `Restart-Service Spooler` |
| `sync_exception` | 改端口过程中的意外 | 看日志，`--force` 重试 |
| `internal` | 兜底异常 | 收日志，跑 `--doctor` |

命令行传密码只在开发时用；GUI 走 `--set-ssid` + 环境变量，密码不会出现在进程命令行。

## 已知边界

- 盒子 USB 是单向转发：没有打印机也照收 TCP 数据，所以“发送成功”不等于“出纸”。
- 手机热点若开 AP 隔离，盒子能上网但电脑找不到它，工具无解，只能换网络形态（USB 共享 / 关隔离）。
- 驱动是 PCL3，不能发纯文本测试页；测试请用驱动渲染，或用 Word 打一页。
