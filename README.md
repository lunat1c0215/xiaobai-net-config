# 小白盒子 BLE 配网工具

Windows 桌面工具：通过 BLE 扫描兼容的小白盒子、读取盒子可见的 Wi-Fi 网络，并下发 Wi-Fi 配网；配网成功后可在局域网内查找盒子 IP，并将指定 Windows 打印机队列的 TCP/IP 端口同步到该 IP。

> **版本：1.0.0** · Windows 10/11 · 支持 XB900WB / XB94* 系列设备。

## 功能

- 扫描附近兼容盒子的 BLE 广播，并读取设备返回的 Wi-Fi 列表。
- 选择 Wi-Fi、输入密码或选择开放网络，然后向盒子下发配网信息。
- 等待盒子加入新 Wi-Fi，在局域网中按设备 MAC 查找 IP。
- 配网流程完成后同步选定打印机队列的端口；也可单独执行“仅同步打印机端口”。
- GUI 展示任务进度与日志；可记住 Wi-Fi 名称和密码，便于下次使用。

## 获取与运行

从 GitHub Releases 下载 `小白盒子配网工具-1.0.0.exe` 和 `ble_config.py`，把两者放在同一目录后运行 EXE。首次启动会在该目录创建 `wifi_history.json`，用于保存本机的 Wi-Fi 历史。

### 运行依赖

此 EXE 是 GUI 启动程序，BLE/网络操作由旁边的 Python 后端完成；**它不是把 Python 运行时也封装进去的完全独立单文件程序**。运行电脑需要：

1. Windows 10/11，且蓝牙适配器可用；
2. Python 3.11 或更新版本，并能从命令行运行 `python`；
3. 安装后端依赖：

   ```powershell
   python -m pip install bleak
   ```

4. BLE 配网时盒子需通电并处于可发现/待配网状态，电脑与盒子保持近距离；
5. 端口同步时，电脑应能在同一局域网内发现盒子。若 Windows 权限或打印机驱动策略要求管理员权限，请按系统提示操作。

源码方式启动：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\xiaobai_gui.ps1
```

## 使用步骤

1. 打开“小白盒子配网工具”。
2. 点击“扫描盒子”，等待 BLE 扫描和 Wi-Fi 列表加载。
3. 选择目标 Wi-Fi；输入密码，或勾选“开放网络（无密码）”。
4. 点击“配网 + 同步打印机”，确认提示。盒子会断开旧网络并尝试连接新网络；密码错误或网络不兼容时可能需要重新配网。
5. 如只需更新打印机端口，使用“仅同步打印机端口”。

## 命令行后端

`ble_config.py` 也可独立调用：

```powershell
python .\ble_config.py --help
python .\ble_config.py --list
python .\ble_config.py --set "Wi-Fi名称" "Wi-Fi密码"
python .\ble_config.py --set-open "开放网络名称"
python .\ble_config.py --only-sync --printer-name "Windows打印机队列名"
```

常用选项：`--addr` 指定盒子 BLE 地址；`--no-sync` 配网后不自动同步端口；`--scan-time` 设置扫描时长；`--printer-name` 指定 Windows 打印机队列名称。执行前可先用 `--help` 查看完整参数。

## Wi-Fi 历史与隐私

- GUI 将密码用 Windows DPAPI `CurrentUser` 范围加密后保存到 `wifi_history.json`；设计上仅可由当前 Windows 用户账户解密。卸载/更换 Windows 账户后，旧记录可能无法解密。
- 文件中仍包含 Wi-Fi 名称和记录时间。可关闭程序后删除 `wifi_history.json` 清除本机历史；此操作不会影响路由器配置。
- 仓库 `.gitignore` 排除该文件、网页抓取快照及反编译输出。

## 仓库内容

- `xiaobai_gui.ps1`：WinForms 图形界面源码。
- `启动小白配网工具.vbs`：无控制台启动器（源码方式）。
- `ble_config.py`：BLE 配网、盒子网络发现与打印机端口同步后端。
- `ble_watch.py`、`watch_state.py`：BLE 通知/状态观察辅助脚本。
- `box_recon.ps1`、`serial_log.ps1`：盒子网络与串口诊断工具。
- `dex*.py`、`ssid_str.py`：针对临时 APK 的协议分析辅助脚本，不是运行 GUI 的必需文件。

## 构建 GUI EXE

在 Windows PowerShell 中安装 `ps2exe` 模块并构建：

```powershell
Install-Module ps2exe -Scope CurrentUser
Import-Module ps2exe
Invoke-ps2exe .\xiaobai_gui.ps1 .\dist\小白盒子配网工具-1.0.0.exe `
  -noConsole -x64 -title '小白盒子配网工具' `
  -product '小白盒子配网工具' -version '1.0.0.0' `
  -description '小白盒子 BLE 配网与打印机端口同步工具'
```

后端仍作为旁置 `ble_config.py` 使用，运行机器仍需 Python 与 `bleak`。Release 的 EXE 与后端脚本需要一起下载/放置。

## 故障排查

- **扫描不到盒子**：确认盒子已上电、蓝牙可发现、处于待配网状态；靠近电脑后重试，并检查 Windows 蓝牙是否开启。
- **盒子没有连上 Wi-Fi**：核对 SSID/密码和路由器频段/加密兼容性；可重新扫描并再次配网。错误密码可能导致盒子暂时失联。
- **未找到新 IP / 端口同步失败**：确认电脑与盒子在同一局域网、允许设备发现；确认 Windows 中的打印机队列名称正确，并检查权限和防火墙设置。
- **后端启动失败**：确认 `python --version` 可用、`python -m pip show bleak` 能找到依赖，并将 EXE 与 `ble_config.py` 放在同一目录。

## 贡献

欢迎通过 GitHub Issues 报告问题或提交改进。
