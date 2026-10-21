#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小白盒子（XB9 系列）配网 + 打印机端口守护 —— 可分发版

设计原则（不依赖任何一台机器上的既有配置）：
  * 盒子：按 BLE 名字前缀扫描，Wi-Fi MAC 从名字后缀推导（XB900WB-C01C303521A3 -> C0:1C:30:35:21:A3）
  * 打印机：不写死队列名，按“端口指向盒子 IP”反查，支持一台盒子多个队列
  * 配置：%LOCALAPPDATA%\\XiaobaiBox\\config.json，首次运行自动生成
  * 改端口前先等作业打完；只有作业卡死才清 Spooler

协议（逆向自 com.mango.ep300.viewmodel.BluetoothVm.v0）：
  payload = Base64(SSID + "||" + PASSWORD) + "#end"，按 18 字符切片写入
  Wi-Fi 列表格式：#split#SSID1||SSID2||...#endsplit

常用命令：
  python xiaobai_box.py --list                 # 读盒子扫到的 Wi-Fi
  python xiaobai_box.py --set "SSID" "密码"     # 配网 + 找 IP + 修端口
  python xiaobai_box.py --sync                  # 只用当前网络修端口
  python xiaobai_box.py --doctor                # 结论式体检
  python xiaobai_box.py --watch                 # 后台守护（IP 变了自动修）
  python xiaobai_box.py --queues                # 看自动认出的队列
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

try:
    from bleak import BleakClient, BleakScanner
except Exception:  # 允许在没装 bleak 的机器上跑 --doctor / --queues
    BleakClient = None
    BleakScanner = None

try:  # Windows 蓝牙无线电开关（可读状态，也可直接打开）
    from winrt.windows.devices.radios import (
        Radio as _Radio, RadioKind as _RadioKind, RadioState as _RadioState)
except Exception:
    _Radio = _RadioKind = _RadioState = None

APP = "XiaobaiBox"
__version__ = "2.0.2"
CH_WRITE = "16fe0d80-c500-11e3-b8c8-0002a5d5c51b"
CH_NOTIFY = "1c927b50-c500-11e3-8a33-0800200c9a66"
CH_NOTIFY2 = "1c927b50-c500-91e3-8a33-0800200c9a66"
CH_READ = "7c927b58-c500-91e3-9a32-2800201c9a6c"
CHUNK = 18
JSON_MARK = "###JSON###"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class BtUnavailable(RuntimeError):
    """蓝牙关着 / 没有适配器。携带可直接展示给用户的结构。"""

    def __init__(self, info: dict):
        super().__init__(info.get("detail") or info.get("state"))
        self.info = info


def fail(code: str, detail="", hint="") -> dict:
    """统一的失败结构：GUI 只认 error code，不解析英文异常。"""
    return {"ok": False, "error": code, "detail": str(detail)[:300], "hint": hint}


def valid_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(str(text))
        return True
    except Exception:
        return False

EMIT_JSON = False
JSON_OUT = None
LOG_OUT = None
LOG_PATH = None

# 被管道/重定向时应输出 UTF-8，避免中文在 GBK 控制台里变乱码
try:
    if hasattr(sys.stdout, "reconfigure") and not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def app_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    d = root / APP
    d.mkdir(parents=True, exist_ok=True)
    return d


CONFIG_PATH = app_dir() / "config.json"

DEFAULT_CONFIG = {
    "box": {
        "name_prefix": "XB9",
        "ble_addr": "",
        "wifi_mac": "",
        "last_ip": "",
        "ip_history": [],
        "last_ssid": "",
    },
    "printer": {
        "queues": [],
        "port": 9100,
        "wait_idle_seconds": 120,
    },
    "watch": {"interval": 300},
}


def _merge(dst: dict, src: dict) -> dict:
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        try:
            _merge(cfg, json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as exc:
            log(f"[!] 配置文件读取失败，使用默认值: {exc}")
    return cfg


def save_config(cfg: dict) -> None:
    try:
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)
    except Exception as exc:
        log(f"[!] 保存配置失败: {exc}")


def log(msg: str = "") -> None:
    try:
        print(msg, flush=True)
    except Exception:
        pass


def progress(pct: int, text: str) -> None:
    print("##PROG##%d|%s" % (int(pct), text), flush=True)


def emit_result(obj: dict) -> None:
    payload = json.dumps(obj, ensure_ascii=False)
    if JSON_OUT:
        try:
            tmp = Path(str(JSON_OUT) + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, JSON_OUT)
        except Exception:
            pass
    if EMIT_JSON:
        print(JSON_MARK + payload, flush=True)


class _Tee:
    def __init__(self, paths):
        if isinstance(paths, str):
            paths = [paths]
        self.files = [open(p, "w", encoding="utf-8", buffering=1) for p in paths]
        self.orig = sys.stdout

    def write(self, s):
        try:
            self.orig.write(s)
        except Exception:
            pass
        for f in self.files:
            try:
                f.write(s)
            except Exception:
                pass
        return len(s)

    def flush(self):
        for f in [self.orig] + self.files:
            try:
                f.flush()
            except Exception:
                pass


def rotate_logs(keep: int = 10) -> None:
    try:
        logs = sorted((app_dir() / "logs").glob("xiaobai-*.log"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        for old in logs[keep:]:
            old.unlink(missing_ok=True)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 网络 / 系统
# --------------------------------------------------------------------------- #
def local_ipv4():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("223.5.5.5", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass


_IP_CACHE = {"t": 0.0, "ips": []}


def local_ipv4_all(max_age: float = 10.0) -> list:
    """所有可用 IPv4（排除回环/169.254）。多网卡、VPN、Hyper-V 场景用。"""
    now = time.time()
    if _IP_CACHE["ips"] and now - _IP_CACHE["t"] < max_age:
        return list(_IP_CACHE["ips"])
    data = ps_json("@(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { "
                   "$_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } "
                   "| Select-Object -ExpandProperty IPAddress) | ConvertTo-Json -Compress")
    if isinstance(data, str):
        out = [data]
    elif isinstance(data, list):
        out = [x for x in data if isinstance(x, str)]
    else:
        out = []
    if not out:
        one = local_ipv4()
        out = [one] if one else []
    _IP_CACHE["t"], _IP_CACHE["ips"] = now, out
    return out


def same_subnet(a: str, b: str) -> bool:
    return bool(a and b and a.rsplit(".", 1)[0] == b.rsplit(".", 1)[0])


def port_open(ip: str, port: int = 9100, timeout: float = 1.5) -> bool:
    if not ip:
        return False
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def arp_table() -> dict:
    try:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True,
                             encoding="gbk", errors="ignore",
                             creationflags=CREATE_NO_WINDOW).stdout
    except Exception:
        return {}
    table = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F:-]{17})", line)
        if m:
            table[m.group(1)] = m.group(2).replace("-", ":").lower()
    return table


def arp_sweep(prefix: str, rounds: int = 3, wait: float = 0.8) -> dict:
    """用 UDP 触发 ARP 解析，然后读 arp -a。不做端口猜测。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for _ in range(rounds):
            for i in range(1, 255):
                try:
                    s.sendto(b"\x00", (f"{prefix}.{i}", 9))
                except Exception:
                    pass
            time.sleep(wait)
    finally:
        s.close()
    return arp_table()


def find_ip_by_mac(mac: str, timeout: int = 20, prefix: str | None = None):
    if not mac:
        return None
    mac = mac.lower().replace("-", ":")
    ip = local_ipv4()
    if not ip:
        return None
    prefix = prefix or ip.rsplit(".", 1)[0]
    deadline = time.time() + timeout
    log(f"[*] 在 {prefix}.0/24 上按 MAC {mac} 找盒子 ...")
    while time.time() < deadline:
        table = arp_sweep(prefix)
        for host, m in table.items():
            if m == mac:
                return host
    return None


def probe_9100(prefix: str, port: int = 9100, timeout: float = 0.8) -> list:
    """没有 MAC 时的兜底：只做 TCP 连接，不发任何打印数据。"""
    from concurrent.futures import ThreadPoolExecutor
    hosts = [f"{prefix}.{i}" for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=96) as pool:
        flags = pool.map(lambda h: (h, port_open(h, port, timeout=timeout)), hosts)
        return [h for h, ok in flags if ok]


def run_ps(script: str, timeout: int = 40) -> str:
    full = ("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
            "$OutputEncoding=[Console]::OutputEncoding;" + script)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", full],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=CREATE_NO_WINDOW)
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return "[timeout]"
    except Exception as exc:
        return f"[error] {exc}"


def ps_json(script: str, default=None):
    txt = run_ps(script).strip()
    if not txt or txt.startswith("[timeout]") or txt.startswith("[error]"):
        return default
    try:
        return json.loads(txt)
    except Exception:
        return default


def ps_text(script: str, default="") -> str:
    txt = run_ps(script).strip()
    if txt.startswith("[timeout]") or txt.startswith("[error]"):
        return default
    return txt or default


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_ps_admin(script: str, timeout: int = 90) -> str:
    """需要管理员时执行一段 PowerShell；非管理员会弹一次 UAC。"""
    if is_admin():
        return run_ps(script, timeout)

    tmpdir = Path(tempfile.mkdtemp(prefix="xb_ps_"))
    job = tmpdir / "job.ps1"
    runner = tmpdir / "run.ps1"
    out = tmpdir / "out.txt"
    job.write_text(
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)\n" + script,
        encoding="utf-8-sig")
    runner.write_text(
        f"$o = & '{job}' 2>&1 | Out-String\n"
        f"[IO.File]::WriteAllText('{out}', $o, [Text.UTF8Encoding]::new($false))\n",
        encoding="utf-8-sig")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Start-Process -FilePath powershell -Verb RunAs -WindowStyle Hidden -Wait "
             f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','{runner}'"],
            capture_output=True, text=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW)
    except Exception as exc:
        return f"[elevate failed] {exc}"
    if not out.exists():
        return "[elevate cancelled] " + (r.stderr or "").strip()[:200]
    try:
        raw = out.read_bytes()
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return raw.decode("utf-16", errors="replace")
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return "[elevate failed: unreadable output]"


def q(name: str) -> str:
    return "'" + str(name).replace("'", "''") + "'"


# --------------------------------------------------------------------------- #
# 打印队列
# --------------------------------------------------------------------------- #
def get_printers() -> list:
    data = ps_json("@(Get-Printer | Select-Object Name,PortName,DriverName,PrinterStatus,JobCount) "
                   "| ConvertTo-Json -Depth 4 -Compress")
    if isinstance(data, dict):
        data = [data]
    return data or []


def get_ports() -> dict:
    data = ps_json("@(Get-PrinterPort | Where-Object { $_.PrinterHostAddress } | "
                   "Select-Object Name,PrinterHostAddress,PortNumber) | ConvertTo-Json -Depth 4 -Compress")
    if isinstance(data, dict):
        data = [data]
    out = {}
    for p in data or []:
        out[p.get("Name")] = {
            "ip": (p.get("PrinterHostAddress") or "").strip(),
            "port": p.get("PortNumber") or 9100,
        }
    return out


def queue_ips(printer: dict, ports: dict) -> str:
    return (ports.get(printer.get("PortName"), {}) or {}).get("ip", "")


def resolve_queues(cfg: dict, ips: list, explicit=None, allow_guess=True) -> list:
    """自动认出所有端口指向盒子 IP 的队列；支持一台盒子多个队列。"""
    printers = get_printers()
    ports = get_ports()
    ips = {i for i in list(ips or []) + list(cfg["box"].get("ip_history") or [])
           + [cfg["box"].get("last_ip")] if i}

    if explicit:
        names = {n.strip().lower() for n in explicit if n and n.strip()}
        picked = [p for p in printers if (p.get("Name") or "").lower() in names]
        if not picked:  # 允许部分匹配
            picked = [p for p in printers
                      if any(n in (p.get("Name") or "").lower() for n in names)]
        return picked

    picked = [p for p in printers if queue_ips(p, ports) in ips]
    if picked:
        return picked

    configured = cfg.get("printer", {}).get("queues") or []
    if configured:
        names = {n.lower() for n in configured}
        hit = [p for p in printers if (p.get("Name") or "").lower() in names]
        if hit:
            return hit

    # 盒子换了 IP：旧端口一定不可达，按“同一失效 IP 上的队列”成组认领
    groups = {}
    for p in printers:
        pip = queue_ips(p, ports)
        if pip and ports.get(p.get("PortName"), {}).get("port") == 9100 \
                and not port_open(pip, 9100, 0.6):
            groups.setdefault(pip, []).append(p)
    if len(groups) == 1:
        return next(iter(groups.values()))
    if cfg["box"].get("last_ip") in groups:
        return groups[cfg["box"]["last_ip"]]

    if allow_guess:
        # 首次在新机器上：只有一个 9100 网络端口队列时，直接认它
        cand = [p for p in printers
                if ports.get(p.get("PortName"), {}).get("port") == 9100]
        if len(cand) == 1:
            return cand
    return []


def default_printer_name():
    name = ps_json("(Get-CimInstance Win32_Printer -Filter 'Default=TRUE').Name")
    return name if isinstance(name, str) else ""


def wait_idle(printer_name: str, timeout: int = 120, poll: float = 1.5) -> tuple:
    """等队列里的作业打完。返回 (是否空闲, 剩余作业数)。"""
    deadline = time.time() + timeout
    while True:
        data = ps_json(f"Get-Printer -Name {q(printer_name)} | Select-Object JobCount | "
                       "ConvertTo-Json -Compress")
        try:
            n = int((data or {}).get("JobCount") or 0)
        except Exception:
            n = 0
        if n == 0:
            return True, 0
        if time.time() >= deadline:
            return False, n
        log(f"[*] {printer_name} 还有 {n} 个作业，等待中（不硬切端口）...")
        progress(88, f"{printer_name} 正在打印，等待作业完成 ...")
        time.sleep(poll)


def switch_port_script(printer_name: str, ip: str, port: int = 9100,
                       stale_ips=None) -> str:
    """生成改端口的 PowerShell。只动 IP_* 端口，不碰 USB/本地端口。"""
    new_port = f"IP_{ip}"
    stale = [x for x in (stale_ips or []) if valid_ip(x) and x != ip]
    stale_lit = "@(" + ",".join(q(x) for x in stale) + ")"
    return f"""
$ErrorActionPreference='Stop'
$res = [ordered]@{{ ok=$true; actions=@(); error=$null }}
try {{
    $pn = {q(printer_name)}
    $newPort = {q(new_port)}
    $stale = {stale_lit}
    $p = Get-Printer -Name $pn -ErrorAction Stop
    if ($p.PortName -ne $newPort) {{
        if (-not (Get-PrinterPort -Name $newPort -ErrorAction SilentlyContinue)) {{
            Add-PrinterPort -Name $newPort -PrinterHostAddress {q(ip)} -PortNumber {port} | Out-Null
            $res.actions += "created:$newPort"
        }}
        Set-Printer -Name $pn -PortName $newPort
        $res.actions += "switched:$pn->$newPort"
    }} else {{
        $res.actions += "already:$pn->$newPort"
    }}
    $used = @(Get-Printer | Select-Object -ExpandProperty PortName)
    Get-PrinterPort | Where-Object {{
        $_.Name -like 'IP_*' -and $_.Name -ne $newPort -and
        ($stale -contains $_.PrinterHostAddress) -and ($used -notcontains $_.Name)
    }} | ForEach-Object {{
        Remove-PrinterPort -Name $_.Name -ErrorAction SilentlyContinue
        $res.actions += "removed:$($_.Name)"
    }}
}} catch {{
    $res.ok = $false
    $res.error = $_.Exception.Message
}}
$res | ConvertTo-Json -Depth 4 -Compress
"""


def sync_queue(printer_name: str, ip: str, port: int = 9100,
               wait_seconds: int = 120, force: bool = False, dry_run: bool = False,
               stale_ips=None) -> dict:
    printers = get_printers()
    cur = next((p for p in printers if p.get("Name") == printer_name), None)
    if not cur:
        return {"ok": False, "printer": printer_name, "error": "队列不存在"}

    if not force:
        idle, left = wait_idle(printer_name, wait_seconds)
        if not idle:
            return {"ok": False, "printer": printer_name,
                    "error": f"作业未清空（剩 {left} 个），本次跳过"}

    ports = get_ports()
    cur_ip = queue_ips(cur, ports)
    new_port = f"IP_{ip}"
    if cur.get("PortName") == new_port:
        return {"ok": True, "printer": printer_name, "ip": ip,
                "actions": ["already"], "changed": False}

    if dry_run:
        return {"ok": True, "printer": printer_name, "ip": ip, "dry_run": True,
                "actions": [f"{cur.get('PortName')} -> {new_port}"], "changed": True}

    stale = list(stale_ips or []) + [cur_ip]
    out = run_ps_admin(switch_port_script(printer_name, ip, port, stale))
    data = None
    for line in reversed((out or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                break
            except Exception:
                continue
    if not data:
        return {"ok": False, "printer": printer_name, "ip": ip,
                "error": (out or "无输出").strip()[:300]}
    data["printer"] = printer_name
    data["ip"] = ip
    data["from"] = cur_ip
    actual = ps_text(f"(Get-Printer -Name {q(printer_name)}).PortName").strip()
    data["verified"] = actual == new_port
    if not data["verified"]:
        data["ok"] = False
        data["error"] = f"写入后端口仍为 {actual or '未知'}"
        data["hint"] = "确认 UAC 点了『是』；仍失败就用管理员 PowerShell 重试"
    return data


def sync_all(cfg: dict, ip: str, explicit=None, force=False, dry_run=False) -> list:
    queues = resolve_queues(cfg, [ip], explicit)
    if not queues:
        log("[!] 没有找到指向该盒子的打印队列")
        return []
    results = []
    for p in queues:
        log(f"[*] 处理队列: {p.get('Name')}  当前端口 {p.get('PortName')}")
        progress(90, f"更新 {p.get('Name')} 的端口 ...")
        try:
            r = sync_queue(p["Name"], ip, force=force, dry_run=dry_run,
                           wait_seconds=int(cfg["printer"].get("wait_idle_seconds") or 120),
                           stale_ips=list(cfg["box"].get("ip_history") or [])
                           + [cfg["box"].get("last_ip") or ""])
        except Exception as exc:
            r = fail("sync_exception", f"{type(exc).__name__}: {exc}",
                     "查看日志；可加 --force 跳过等待后重试")
        if r.get("ok"):
            log("[+] " + p["Name"] + " -> " + ", ".join(map(str, r.get("actions") or [])))
        else:
            log("[X] " + p["Name"] + " 失败: " + str(r.get("error")))
        results.append(r)
    return results


# --------------------------------------------------------------------------- #
# 盒子 BLE
# --------------------------------------------------------------------------- #
async def bt_status(auto_on: bool = True, wait: float = 6.0) -> dict:
    """读 Windows 蓝牙开关状态；关着时尝试自动打开。

    这是配网前必须做的预检：蓝牙关掉时 bleak 会直接抛
    BleakBluetoothNotAvailableError，用户只看到一堆英文堆栈。
    """
    if _Radio is None:
        return await _bt_functional()
    try:
        radios = await _Radio.get_radios_async()
    except Exception as exc:
        return {"state": "unknown", "detail": f"读取蓝牙状态失败: {exc}"[:200]}

    bt = [r for r in radios if r.kind == _RadioKind.BLUETOOTH]
    if not bt:
        return {"state": "absent", "detail": "没有检测到蓝牙适配器",
                "hint": "插上蓝牙适配器，或换一台带蓝牙的电脑"}
    if any(r.state == _RadioState.ON for r in bt):
        return {"state": "on", "detail": bt[0].name, "auto": False}

    info = {"state": "off", "detail": f"{bt[0].name} 处于关闭状态",
            "hint": "设置 > 蓝牙和其他设备，把蓝牙打开"}
    if not auto_on:
        return info

    tried = False
    for r in bt:
        if r.state in (_RadioState.OFF, _RadioState.DISABLED):
            try:
                await r.set_state_async(_RadioState.ON)
                tried = True
            except Exception as exc:
                info["set_error"] = str(exc)[:160]
    deadline = time.time() + wait
    while tried and time.time() < deadline:
        await asyncio.sleep(0.4)
        try:
            radios = await _Radio.get_radios_async()
        except Exception:
            break
        bt = [r for r in radios if r.kind == _RadioKind.BLUETOOTH]
        if any(r.state == _RadioState.ON for r in bt):
            return {"state": "on", "detail": bt[0].name, "auto": True}
    info["auto_tried"] = tried
    return info


async def _bt_functional() -> dict:
    """没有 winrt 时的功能探测：真扫一次，从异常里判断是不是蓝牙关着。"""
    if BleakScanner is None:
        return {"state": "unknown", "detail": "缺少 bleak 组件"}
    try:
        await BleakScanner.discover(timeout=1.5)
        return {"state": "on", "detail": "扫描可用"}
    except Exception as exc:
        msg = str(exc)
        low = msg.lower()
        if any(k in low for k in ("powered on", "powered off", "radio", "turn on")):
            return {"state": "off", "detail": msg[:200],
                    "hint": "设置 > 蓝牙和其他设备，把蓝牙打开"}
        return {"state": "error", "detail": msg[:200]}


def mac_from_name(name: str) -> str:
    """XB900WB-C01C303521A3 -> C0:1C:30:35:21:A3"""
    if not name:
        return ""
    m = re.search(r"-([0-9A-Fa-f]{12})$", name.strip())
    if not m:
        return ""
    h = m.group(1).upper()
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def _prefixes(cfg: dict) -> tuple:
    raw = (cfg["box"].get("name_prefix") or "XB9").split(",")
    return tuple(x.strip().upper() for x in raw if x.strip()) or ("XB9",)


async def scan_box(cfg: dict, timeout: int = 25, quiet: bool = False):
    if BleakScanner is None:
        log("[!] 没安装 bleak：pip install bleak")
        return None
    st = await bt_status(auto_on=True)
    if st.get("state") in ("unknown", "error"):
        st2 = await _bt_functional()
        if st2.get("state") == "on":
            st = st2
    if st.get("state") != "on":
        if st.get("auto_tried"):
            log("[!] 电脑蓝牙是关着的，尝试自动打开失败")
        elif st.get("state") == "absent":
            log("[!] 这台电脑没有蓝牙适配器")
        else:
            log("[!] 电脑蓝牙未打开")
        if st.get("hint"):
            log("    " + st["hint"])
        raise BtUnavailable(st)
    if st.get("auto"):
        log("[!] 电脑蓝牙原为关闭，已自动打开")
    prefix = _prefixes(cfg)
    found = {}

    def cb(dev, adv):
        nm = dev.name or getattr(adv, "local_name", "") or ""
        if any(nm.upper().startswith(p) for p in prefix):
            found[dev.address] = dev

    if not quiet:
        log(f"[*] 扫描蓝牙 {timeout} 秒，找 {'/'.join(prefix)} ...")
    progress(10, "扫描蓝牙 ...")
    try:
        async with BleakScanner(detection_callback=cb):
            await asyncio.sleep(timeout)
    except Exception as exc:
        log(f"[!] 蓝牙扫描失败: {exc}")
        return None

    if not found:
        log("[!] 没扫到盒子（可能没电 / 不在附近 / 已被 App 占用蓝牙）")
        return None
    lst = sorted(found.values(),
                 key=lambda d: getattr(d, "rssi", -999) or -999, reverse=True)
    dev = lst[0]
    name = dev.name or ""
    if name:
        cfg["box"]["ble_addr"] = dev.address
        mac = mac_from_name(name)
        if mac:
            cfg["box"]["wifi_mac"] = mac
        save_config(cfg)
    log(f"[+] 找到盒子: {name}({dev.address})  Wi-Fi MAC={cfg['box'].get('wifi_mac') or '未识别'}")
    progress(30, "已找到盒子")
    return dev


def _parse_ssids(raw: str) -> list:
    if "#split#" not in raw:
        return []
    inner = raw.split("#split#", 1)[1].split("#endsplit", 1)[0]
    return [x for x in inner.split("||") if x.strip()]


async def read_box(dev, timeout: int = 20) -> dict:
    buf = []
    done = asyncio.Event()
    try:
        async with BleakClient(dev, timeout=25) as c:
            log("[+] 已连接: %s" % c.is_connected)
            progress(50, "已连接，读取盒子状态 ...")
            try:
                code = await c.read_gatt_char(CH_READ)
                log("[*] bindCode: %r" % bytes(code))
            except Exception:
                pass

            def on_data(_ch, data):
                buf.append(bytes(data).decode("utf-8", "ignore"))
                if "#endsplit" in "".join(buf):
                    done.set()

            try:
                await c.start_notify(CH_NOTIFY, on_data)
            except Exception as exc:
                return fail("ble_subscribe_failed", f"{type(exc).__name__}: {exc}",
                            "关掉手机上的小白 App（会占用蓝牙），再靠近盒子重试")
            try:
                await c.start_notify(CH_NOTIFY2, on_data)
            except Exception:
                pass
            log("[*] 等待盒子推送 Wi-Fi 列表 ...")
            progress(65, "等待盒子上报 Wi-Fi 列表 ...")
            try:
                await asyncio.wait_for(done.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return fail("ble_timeout", "等待 Wi-Fi 列表超时: %r" % "".join(buf)[:120],
                            "盒子可能已配好网；断电重启后再扫描")
            raw = "".join(buf)
            ssids = _parse_ssids(raw)
            for i, n in enumerate(ssids, 1):
                log(f"    {i:2d}. {n}")
            return {"ok": True, "ssids": ssids, "raw": raw}
    except asyncio.TimeoutError:
        return fail("ble_timeout", "连接盒子超时", "把盒子靠近电脑，确认已通电")
    except Exception as exc:
        return fail("ble_connect_failed", f"{type(exc).__name__}: {exc}",
                    "确认盒子通电、距离 10 米内，并关掉手机 App 的蓝牙占用")


async def pair_box(dev, ssid: str, pwd: str, timeout: int = 25,
                   delay: float = 0.25, urlenc: bool = False) -> dict:
    raw = ssid + "||" + pwd
    payload = (urllib.parse.quote(raw, safe="") if urlenc
               else base64.b64encode(raw.encode("utf-8")).decode("ascii"))
    full = payload + "#end"
    chunks = [full[i:i + CHUNK] for i in range(0, len(full), CHUNK)]
    log(f"[*] SSID: {ssid}   载荷 {len(full)} 字符 / {len(chunks)} 段")

    notes = []
    net = {"state": None}
    up = asyncio.Event()
    sent = 0
    try:
        async with BleakClient(dev, timeout=25) as c:
            log("[+] 已连接: %s" % c.is_connected)

            def on_note(_ch, data):
                t = bytes(data).decode("utf-8", "ignore")
                notes.append(t)
                log("    [应答] %r" % t)
                if t in ("true", "false"):
                    net["state"] = t
                    if t == "true":
                        up.set()

            try:
                await c.start_notify(CH_NOTIFY, on_note)
                await c.start_notify(CH_NOTIFY2, on_note)
            except Exception:
                pass

            for i, ch in enumerate(chunks, 1):
                await c.write_gatt_char(CH_WRITE, ch.encode("utf-8"), response=True)
                sent = i
                progress(35 + int(25 * i / len(chunks)), f"下发配网数据 {i}/{len(chunks)}")
                await asyncio.sleep(delay)

            progress(60, "已下发，等待盒子联网状态 ...")
            try:
                await asyncio.wait_for(up.wait(), timeout=timeout)
                log("[+] 盒子上报 true -> 已联网")
            except asyncio.TimeoutError:
                log(f"[!] {timeout} 秒内没有 true")
    except Exception as exc:
        if net["state"] == "true":
            return {"ok": True, "net_up": True, "state": "true", "replies": notes,
                    "detail": "盒子已联网，随后断开了蓝牙（正常）"}
        return fail("ble_disconnected",
                    f"{type(exc).__name__}: {exc}（已发送 {sent}/{len(chunks)} 段）",
                    "盒子可能重启了：等 10 秒重新扫描；若已发完通常仍然生效")
    return {"ok": True, "net_up": net["state"] == "true", "state": net["state"],
            "replies": notes}


async def ble_alive(cfg: dict, seconds: int = 8) -> bool:
    try:
        dev = await scan_box(cfg, timeout=seconds, quiet=True)
    except BtUnavailable:
        return False
    return dev is not None


# --------------------------------------------------------------------------- #
# 找盒子 IP
# --------------------------------------------------------------------------- #
def locate_box(cfg: dict, allow_probe: bool = True, arp_timeout: int = 18) -> dict:
    ips = local_ipv4_all()
    if not ips:
        return {"ok": False, "error": "本机没有可用 IPv4（先连 Wi-Fi / 网线）"}
    ip = ips[0]
    # 默认路由所在网段优先；其余网段排在后面，避免 VPN/虚拟网卡抢时间
    prefixes = []
    for x in [local_ipv4()] + ips:
        if not x:
            continue
        pfx = x.rsplit(".", 1)[0]
        if pfx not in prefixes:
            prefixes.append(pfx)

    last = cfg["box"].get("last_ip") or ""
    if last and any(same_subnet(last, x) for x in ips) and port_open(last, 9100):
        log(f"[+] 上次记录的 IP 仍可用: {last}")
        remember_ip(cfg, last)
        return {"ok": True, "ip": last, "via": "cache"}

    mac = cfg["box"].get("wifi_mac") or ""
    if mac:
        progress(20, "按 MAC 查找盒子 IP ...")
        t_end = time.time() + arp_timeout
        for i, pfx in enumerate(prefixes):
            left = t_end - time.time()
            if left < 3:
                break
            per = max(3, left / max(1, len(prefixes) - i))
            found = find_ip_by_mac(mac, timeout=int(per), prefix=pfx)
            if found:
                remember_ip(cfg, found)
                log(f"[+] 盒子当前 IP: {found}")
                return {"ok": True, "ip": found, "via": "arp"}

    if allow_probe:
        log("[*] ARP 没找到，改用 9100 端口探测（只连不发数据）...")
        cands = []
        probe_end = time.time() + 6
        for pfx in prefixes:
            if time.time() > probe_end:
                break
            for c in probe_9100(pfx, timeout=0.5):
                if c not in cands and c not in ips:
                    cands.append(c)
        if len(cands) == 1:
            remember_ip(cfg, cands[0])
            log(f"[+] 唯一候选: {cands[0]}")
            return {"ok": True, "ip": cands[0], "via": "probe"}
        if len(cands) > 1:
            log("[!] 有多个 9100 候选，需要人工指定 --ip: " + ", ".join(cands))
            return {"ok": False, "error": "multiple candidates", "candidates": cands}

    return {"ok": False, "error": "box not found",
            "hint": "盒子不在本网段 / 未通电 / 未配网"}


def remember_ip(cfg: dict, ip: str) -> None:
    if not ip:
        return
    cfg["box"]["last_ip"] = ip
    hist = [x for x in (cfg["box"].get("ip_history") or []) if x != ip]
    cfg["box"]["ip_history"] = ([ip] + hist)[:6]
    save_config(cfg)


# --------------------------------------------------------------------------- #
# 体检
# --------------------------------------------------------------------------- #
def doctor(cfg: dict) -> dict:
    checks = []

    def add(name, ok, detail, fix="", critical=True):
        checks.append({"name": name, "ok": bool(ok), "detail": detail,
                       "fix": fix, "critical": critical})

    spooler = ps_text("(Get-Service Spooler).Status")
    add("Spooler 服务", spooler == "Running", spooler or "未知",
        "管理员运行: Restart-Service Spooler")

    local = local_ipv4()
    add("本机已联网", bool(local), local or "无 IPv4", "先连 Wi-Fi 或网线")

    bt = asyncio.run(bt_status(auto_on=False))
    add("电脑蓝牙", bt.get("state") == "on",
        bt.get("detail") or bt.get("state"),
        "打开蓝牙；用「扫描盒子」时程序也会尝试自动打开", critical=False)

    loc = locate_box(cfg, arp_timeout=8)
    box_ip = loc.get("ip")
    add("盒子在线", bool(box_ip), box_ip or str(loc.get("error")),
        "盒子不在本网段：给它重新配网，或把电脑切到盒子所在网络")

    queues = resolve_queues(cfg, [box_ip, cfg["box"].get("last_ip")]) if box_ip else \
        resolve_queues(cfg, [cfg["box"].get("last_ip")])
    add("找到打印队列", bool(queues), ", ".join(p["Name"] for p in queues) or "无",
        "用 --queue 指定 Word 里那个队列")

    if box_ip and queues:
        ports = get_ports()
        q_ips = [queue_ips(p, ports) for p in queues]
        add("队列端口指向盒子", box_ip in q_ips, f"{q_ips} vs {box_ip}",
            "运行 --sync 修端口")
        add("网段一致", same_subnet(box_ip, local or ""), f"{local} / {box_ip}",
            "电脑和盒子必须在同一个 Wi-Fi")
        add("9100 可达", port_open(box_ip, 9100), f"{box_ip}:9100",
            "盒子没把端口开出来：重启盒子 / 检查 USB 供电")

        busy = []
        for p in queues:
            try:
                n = int((ps_json(f"Get-Printer -Name {q(p['Name'])} | Select-Object JobCount | "
                                 "ConvertTo-Json -Compress") or {}).get("JobCount") or 0)
            except Exception:
                n = 0
            if n:
                busy.append(f"{p['Name']}:{n}")
        add("无卡死作业", not busy, ", ".join(busy) or "队列为空",
            "管理员运行 修复打印队列.ps1")

    ok = all(c["ok"] for c in checks if c.get("critical", True))
    if ok:
        conclusion = "Windows 侧链路正常。Word 里选 %s；若仍不出纸，问题在盒子到打印机（USB/供电）。" % \
                     (queues[0]["Name"] if queues else "对应队列")
    elif not box_ip:
        conclusion = "盒子不在当前网络：先在工具里重新配网，或把电脑连到盒子所在 Wi-Fi。"
    else:
        bad = [c["name"] for c in checks if not c["ok"]]
        conclusion = "未通过: " + "、".join(bad) + "。按每项后面的修法处理。"

    return {"ok": ok, "checks": checks, "conclusion": conclusion,
            "box_ip": box_ip, "queues": [p["Name"] for p in queues]}


def print_doctor(res: dict) -> None:
    for c in res["checks"]:
        log(("[OK] " if c["ok"] else "[X ] ") + c["name"] + "  " + c["detail"])
        if not c["ok"] and c["fix"]:
            log("      -> " + c["fix"])
    log("")
    log("结论: " + res["conclusion"])


# --------------------------------------------------------------------------- #
# 守护
# --------------------------------------------------------------------------- #
def watch_loop(cfg: dict, interval: int, explicit=None) -> int:
    log(f"[*] 守护启动，每 {interval} 秒检查一次；Ctrl+C 退出")
    while True:
        try:
            ip = cfg["box"].get("last_ip") or ""
            if ip and port_open(ip, 9100):
                time.sleep(interval)
                continue
            log("[!] 盒子 IP 不可达，重新定位 ...")
            loc = locate_box(cfg)
            if loc.get("ok"):
                new_ip = loc["ip"]
                if new_ip != ip:
                    log(f"[*] IP 变化: {ip or '(无)'} -> {new_ip}")
                    res = sync_all(cfg, new_ip, explicit)
                    emit_result({"ok": True, "mode": "watch", "old": ip,
                                 "new": new_ip, "results": res})
                else:
                    log("[*] IP 未变，但 9100 不通：检查盒子/打印机供电")
            else:
                log("[!] 仍找不到盒子：" + str(loc.get("hint") or loc.get("error")))
        except KeyboardInterrupt:
            log("[*] 已退出守护")
            return 0
        except Exception as exc:
            log(f"[!] 守护异常: {exc}")
        time.sleep(interval)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def cmd_sync(cfg, args, ips=None) -> int:
    loc = None
    if ps_text("(Get-Service Spooler).Status") not in ("Running", ""):
        res = fail("spooler_stopped", "打印后台服务 Spooler 没有运行",
                   "管理员运行: Restart-Service Spooler")
        log("[X] " + res["detail"])
        emit_result(res)
        return 3
    if args.ip:
        if not valid_ip(args.ip):
            res = fail("bad_ip", f"不是合法 IP: {args.ip}", "例：--ip 192.168.1.50")
            log("[X] " + res["detail"])
            emit_result(res)
            return 2
        ip = args.ip
        if port_open(ip, 9100):
            remember_ip(cfg, ip)
    else:
        loc = locate_box(cfg, allow_probe=not args.no_probe)
        ip = loc.get("ip")
    if not ip:
        res = {"ok": False, "mode": "sync",
               "error": (loc or {}).get("error") or "box not found",
               "candidates": (loc or {}).get("candidates")}
        log("[!] 找不到盒子 IP")
        if res.get("candidates"):
            log("    候选: " + ", ".join(res["candidates"]) + "  用 --ip 指定")
        emit_result(res)
        return 1

    queues = resolve_queues(cfg, [ip], args.queue)
    if queues:
        log("[*] 目标队列: " + ", ".join(p["Name"] for p in queues))
    res = sync_all(cfg, ip, args.queue, force=args.force, dry_run=args.dry_run)
    ok = bool(res) and all(r.get("ok") for r in res)
    if not res:
        log("[!] 没有可修的队列：确认盒子已在该网络，或用 --queue 指定")
        pr = get_printers()
        if pr:
            log("    现有队列: " + ", ".join(f"{p['Name']}({p['PortName']})" for p in pr))
    emit_result({"ok": ok, "mode": "sync", "ip": ip, "results": res,
                 "queues": [p["Name"] for p in queues]})
    progress(100, "完成")
    return 0 if ok else 1


def cmd_pair(cfg, args) -> int:
    async def run():
        if args.set_open is not None:
            ssid, pwd = args.set_open, ""
        elif args.set:
            ssid, pwd = args.set
        else:
            ssid = args.set_ssid
            pwd = os.environ.get(args.pwd_env or "XB_WIFI_PWD", "")
            if not pwd:
                res = fail("no_password", "没有从环境变量拿到 Wi-Fi 密码",
                           "重新输入密码；开放网络请用 --set-open")
                log("[X] " + res["detail"])
                emit_result({"mode": "set+sync", **res})
                return 1

        try:
            dev = await scan_box(cfg, timeout=args.scan_time)
        except BtUnavailable as exc:
            info = exc.info
            emit_result({"ok": False, "mode": "scan",
                         "error": "bluetooth_off" if info.get("state") == "off"
                         else "bluetooth_" + str(info.get("state")),
                         **info})
            return 1
        if not dev:
            emit_result({"ok": False, "mode": "scan", "error": "box not found"})
            return 1

        cfg["box"]["last_ssid"] = ssid
        save_config(cfg)

        res = await pair_box(dev, ssid, pwd, timeout=args.net_timeout,
                             delay=args.delay, urlenc=args.urlenc)
        if not res.get("ok") and res.get("error") != "ble_disconnected":
            log("[X] 配网下发失败: " + str(res.get("detail")))
            if res.get("hint"):
                log("    " + res["hint"])
            emit_result({"mode": "set+sync", **res})
            return 1
        progress(78, "配网指令完成，等待盒子接入新网络 ...")

        if not args.no_sync:
            if not res.get("net_up"):
                await asyncio.sleep(12)
            loc = None
            for _ in range(3):
                await asyncio.sleep(4)
                loc = locate_box(cfg, arp_timeout=6)
                if loc.get("ok"):
                    break
            if loc and loc.get("ok"):
                ip = loc["ip"]
                log(f"[+] 盒子新 IP: {ip}")
                progress(90, "更新打印机端口 ...")
                results = sync_all(cfg, ip, args.queue, force=args.force,
                                   dry_run=args.dry_run)
                emit_result({"ok": bool(results), "mode": "set+sync", "ip": ip,
                             "net_up": res.get("net_up"), "state": res.get("state"),
                             "results": results})
            else:
                alive = await ble_alive(cfg, 8)
                log("[!] 配网后没有找到盒子 IP —— 判定配网失败")
                log("    蓝牙在线: %s（在线就可直接再配一次）" % ("是" if alive else "否"))
                emit_result({"ok": False, "mode": "set+sync", "error": "ip not found",
                             "net_up": res.get("net_up"), "state": res.get("state"),
                             "ble_alive": alive})
                return 1
        else:
            emit_result({"ok": True, "mode": "set", **res})
        progress(100, "完成")
        return 0

    return asyncio.run(run())


def cmd_diagnostics(cfg, args) -> int:
    """把排查需要的东西打成一个 zip，用户发过来就能定位。"""
    import platform
    import zipfile

    ts = time.strftime("%Y%m%d-%H%M%S")
    target = args.diagnostics
    if target is True or not target:
        desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
        if not desktop.exists():
            desktop = app_dir()
        target = desktop / f"xiaobai-diag-{ts}.zip"
    target = Path(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    doc = doctor(cfg)
    print_doctor(doc)
    printers = get_printers()
    ports = get_ports()
    bt = asyncio.run(bt_status(auto_on=False))
    info = "\n".join([
        f"version: {__version__}",
        f"time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"platform: {platform.platform()}",
        f"python: {sys.version.split()[0]}",
        f"frozen: {getattr(sys, 'frozen', False)}",
        f"exe: {sys.executable}",
        f"cwd: {os.getcwd()}",
        f"admin: {is_admin()}",
        f"local_ipv4: {local_ipv4_all()}",
        f"bluetooth: {bt}",
        f"log: {LOG_PATH or '(none)'}",
    ])
    tail = ""
    try:
        if LOG_PATH and Path(LOG_PATH).exists():
            tail = Path(LOG_PATH).read_text(encoding="utf-8", errors="replace")[-120000:]
    except Exception:
        pass

    try:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("info.txt", info)
            z.writestr("config.json", json.dumps(cfg, ensure_ascii=False, indent=2))
            z.writestr("doctor.json", json.dumps(doc, ensure_ascii=False, indent=2))
            z.writestr("printers.json",
                       json.dumps({"printers": printers, "ports": ports},
                                  ensure_ascii=False, indent=2))
            z.writestr("log.txt", tail)
    except Exception as exc:
        res = fail("diag_failed", f"{type(exc).__name__}: {exc}", "换一个可写目录重试")
        log("[X] " + res["detail"])
        emit_result(res)
        return 1

    log("[+] 诊断包已生成: " + str(target))
    emit_result({"ok": True, "mode": "diagnostics", "path": str(target)})
    return 0


def main() -> int:
    global EMIT_JSON, JSON_OUT, LOG_PATH
    ap = argparse.ArgumentParser(description="小白盒子配网 + 打印机端口工具（可分发版）")
    ap.add_argument("--version", action="version", version=f"XiaobaiBox {__version__}")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--list", action="store_true", help="读盒子扫到的 Wi-Fi 列表")
    g.add_argument("--set", nargs=2, metavar=("SSID", "PASSWORD"), help="配网")
    g.add_argument("--set-ssid", metavar="SSID",
                   help="配网（密码从环境变量读，避免出现在进程命令行）")
    g.add_argument("--set-open", metavar="SSID", help="配到开放网络")
    g.add_argument("--sync", "--only-sync", dest="sync", action="store_true",
                   help="只同步打印机端口")
    g.add_argument("--doctor", action="store_true", help="结论式体检")
    g.add_argument("--queues", action="store_true", help="列出自动认出的队列")
    g.add_argument("--watch", action="store_true", help="后台守护，IP 变化自动修端口")
    g.add_argument("--bt-status", action="store_true",
                   help="检查电脑蓝牙；关着会自动尝试打开")
    g.add_argument("--diagnostics", nargs="?", const=True, metavar="ZIP",
                   help="生成诊断包（默认保存到桌面）")

    ap.add_argument("--queue", action="append", help="指定队列名（可多次；默认自动反查）")
    ap.add_argument("--ip", help="直接指定盒子 IP")
    ap.add_argument("--interval", type=int, help="守护间隔秒（默认 300）")
    ap.add_argument("--force", action="store_true", help="不等待作业，强制切端口")
    ap.add_argument("--dry-run", action="store_true", help="只显示计划，不改系统")
    ap.add_argument("--no-probe", action="store_true", help="不做 9100 全网段探测")
    ap.add_argument("--no-auto-on", action="store_true", help="不要自动打开蓝牙")
    ap.add_argument("--no-sync", action="store_true", help="配网后不同步端口")
    ap.add_argument("--scan-time", type=int, default=25)
    ap.add_argument("--net-timeout", type=int, default=25)
    ap.add_argument("--delay", type=float, default=0.25)
    ap.add_argument("--urlenc", action="store_true", help="旧协议编码方式")
    ap.add_argument("--pwd-env", default="XB_WIFI_PWD",
                    help="配合 --set-ssid 使用的环境变量名")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--json-out")
    ap.add_argument("--log-out")
    ap.add_argument("--show-config", action="store_true")
    args = ap.parse_args()

    EMIT_JSON = args.json
    JSON_OUT = args.json_out
    log_dir = app_dir() / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    LOG_PATH = str(log_dir / f"xiaobai-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.log")
    paths = [LOG_PATH]
    if args.log_out:
        paths.append(args.log_out)
    sys.stdout = _Tee(paths)
    rotate_logs(10)

    cfg = load_config()

    if args.show_config:
        log(str(CONFIG_PATH))
        log(json.dumps(cfg, ensure_ascii=False, indent=2))
        emit_result({"ok": True, "config": cfg, "path": str(CONFIG_PATH)})
        return 0

    if args.doctor:
        res = doctor(cfg)
        print_doctor(res)
        emit_result({"mode": "doctor", **res})
        return 0 if res["ok"] else 2

    if args.bt_status:
        st = asyncio.run(bt_status(auto_on=not args.no_auto_on))
        label = {"on": "已开启", "off": "未开启", "absent": "无适配器",
                 "unknown": "未知", "error": "异常"}.get(st.get("state"), st.get("state"))
        log(f"[{'OK' if st.get('state') == 'on' else 'X '}] 电脑蓝牙: {label}  "
            f"{st.get('detail') or ''}")
        if st.get("hint"):
            log("    " + st["hint"])
        emit_result({"mode": "bt", **st})
        return 0 if st.get("state") == "on" else 1

    if args.diagnostics is not None:
        return cmd_diagnostics(cfg, args)

    if args.queues:
        printers = get_printers()
        ports = get_ports()
        ips = [cfg["box"].get("last_ip"), args.ip]
        qs = resolve_queues(cfg, ips)
        for p in printers:
            log(f"{'*' if p in qs else ' '} {p['Name']}  {p['PortName']}  "
                f"{queue_ips(p, ports)}  jobs={p.get('JobCount')}")
        emit_result({"ok": True, "mode": "queues",
                     "queues": [p["Name"] for p in qs],
                     "printers": [{"name": p["Name"], "port": p["PortName"],
                                   "ip": queue_ips(p, ports)} for p in printers]})
        return 0

    if args.sync:
        return cmd_sync(cfg, args)

    if args.watch:
        return watch_loop(cfg, args.interval or int(cfg["watch"].get("interval") or 300),
                          args.queue)

    if args.set or args.set_ssid is not None or args.set_open is not None:
        return cmd_pair(cfg, args)

    if args.list:
        async def run():
            try:
                dev = await scan_box(cfg, timeout=args.scan_time)
            except BtUnavailable as exc:
                info = exc.info
                emit_result({"ok": False, "mode": "scan",
                             "error": "bluetooth_off" if info.get("state") == "off"
                             else "bluetooth_" + str(info.get("state")),
                             **info})
                return 1
            if not dev:
                emit_result({"ok": False, "mode": "scan", "error": "box not found"})
                return 1
            res = await read_box(dev)
            if not res.get("ok"):
                log("[X] 读取失败: " + str(res.get("detail")))
                if res.get("hint"):
                    log("    " + res["hint"])
                emit_result({"mode": "list", **res})
                return 1
            emit_result({"ok": True, "mode": "list", "name": dev.name,
                         "addr": dev.address,
                         "wifi_mac": cfg["box"].get("wifi_mac"),
                         "ssids": res.get("ssids", [])})
            progress(100, "完成")
            return 0
        return asyncio.run(run())

    ap.print_help()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("[*] 已中断")
        sys.exit(130)
    except Exception as exc:
        # 最后一道网：绝不让用户看到裸堆栈
        log(f"[X] 内部错误: {type(exc).__name__}: {exc}")
        emit_result(fail("internal", f"{type(exc).__name__}: {exc}",
                         "把日志发出来；可先运行 --doctor 看环境"))
        sys.exit(3)
