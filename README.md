# 小白盒子 BLE 配网工具

Windows 端小白盒子（XB900WB / XB94*）BLE 配网工具及配套诊断脚本。

## 文件

- `xiaobai_gui.ps1`：Windows 原生 GUI
- `ble_config.py`：BLE 配网实现
- `ble_watch.py`、`watch_state.py`：BLE 状态观察
- `box_recon.ps1`、`serial_log.ps1`：网络与串口诊断
- `启动小白配网工具.vbs`：无控制台启动器
- `dex*.py`、`ssid_str.py`：针对临时 APK 的协议分析辅助脚本

## 运行提示

运行 GUI 需要 Windows PowerShell、Python 和 `bleak`。请按实际设备环境配置并测试；配网功能需要兼容的 BLE 设备。

本仓库仅包含项目源码与辅助脚本，不包含本机 Wi-Fi 历史、抓取网页或反编译输出文件。
