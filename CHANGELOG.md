# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循语义化版本。

## [2.0.2] - 2026-10-22

### 新增

- 日志持久化到 `%LOCALAPPDATA%\XiaobaiBox\logs\`，自动保留最近 10 份
- 面板新增「导出诊断包」：版本、系统、蓝牙状态、队列、体检结果、最近日志打成一个 zip
- `--diagnostics [ZIP]` 命令行入口，默认输出到桌面
- `--version` 与单一版本源（`xiaobai_box.py` 的 `__version__`）
- 安装/卸载入口（当前用户目录，无需管理员）
- `requirements.txt` / `requirements-build.txt` 锁定依赖
- `SHA256SUMS` 随 Release 发布

### 改进

- 高 DPI 感知，125% / 150% 缩放下界面不再位图拉伸
- 打包脚本从引擎读取版本号，不再手写

## [2.0.1] - 2026-10-22

### 新增

- 面板「使用说明」按钮，打开 `使用说明.html`
- 说明书补充手机热点必须使用 2.4GHz（安卓 AP 频段 / iPhone 最大兼容性）

## [2.0.0] - 2026-10-22

### 新增

- 可分发引擎 `XiaobaiBox.exe`（自带 Python + bleak，目标机无需安装 Python）
- 按端口 IP 自动反查打印队列，支持一台盒子多个队列
- 切端口前等待作业清空，切完回读验证，旧端口清理限定在本盒子历史 IP
- 蓝牙预检：关闭时询问并自动打开；无适配器单独提示
- 结构化错误码（`bluetooth_off` / `ble_timeout` / `bad_ip` / `spooler_stopped` 等）
- 多网卡发现，时间预算封顶；`--doctor` 结论式体检；`--watch` 守护
- GUI 传密码改用环境变量，不再出现在进程命令行

## [1.0.0] - 2026-10-10

### 新增

- 初版：BLE 扫描、Wi-Fi 列表、配网、按 MAC 找 IP、同步打印机端口
