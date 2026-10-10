"""
小白盒子 (XB900WB / XB94*) 电脑端 BLE 配网工具

协议来自 com.mango.ep300.viewmodel.BluetoothVm.v0(String,String,String):
    payload = urlencode(SSID + "||" + PASSWORD) + "#end"
    payload 按 18 字符切片, 顺序写入特征值 16fe0d80-c500-11e3-b8c8-0002a5d5c51b
    应答走 notify 1c927b50-c500-11e3-8a33-0800200c9a66
Wi-Fi 列表上报格式: #split#SSID1||SSID2||...#endsplit

用法:
    python ble_config.py --list
    python ble_config.py --set "MyWiFi" "mypassword"
    python ble_config.py --set "MyWiFi" "mypassword" --addr 60:DE:F4:4E:42:57 --plus-space
"""
import argparse
import asyncio
import base64
import json
import re
import socket
import subprocess
import sys
import urllib.parse

from bleak import BleakClient, BleakScanner

SVC_WRITE = "09fc95c0-c500-11e3-9904-0002a5d5c51b"
CH_WRITE = "16fe0d80-c500-11e3-b8c8-0002a5d5c51b"
CH_NOTIFY = "1c927b50-c500-11e3-8a33-0800200c9a66"
CH_NOTIFY2 = "1c927b50-c500-91e3-8a33-0800200c9a66"
CH_READ = "7c927b58-c500-91e3-9a32-2800201c9a6c"

NAME_PREFIXES = ("XB900", "XB94", "XB9")
CHUNK = 18
JSON_MARK = "###JSON###"
EMIT_JSON = False
JSON_OUT = None


def emit(obj):
    payload = json.dumps(obj, ensure_ascii=False)
    if JSON_OUT:
        try:
            with open(JSON_OUT, "w", encoding="utf-8") as f:
                f.write(payload)
        except Exception:
            pass
    if EMIT_JSON:
        print(JSON_MARK + payload)


def emit_prog(pct, text):
    """给 GUI 用的进度标记（会被 GUI 从日志里滤掉）"""
    print("##PROG##%d|%s" % (int(pct), text))

BOX_WIFI_MAC = "c0:1c:30:35:21:a3"   # 从 BLE 名字 XB900WB-C01C303521A3 得到


# ---------- 打印机配置同步 ----------

def local_ipv4():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("223.5.5.5", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def arp_table():
    out = subprocess.run(["arp", "-a"], capture_output=True, text=True,
                         encoding="gbk", errors="ignore").stdout
    table = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F:-]{17})", line)
        if m:
            table[m.group(1)] = m.group(2).replace("-", ":").lower()
    return table


def find_ip_by_mac(mac=BOX_WIFI_MAC, timeout=40):
    mac = mac.lower()
    ip = local_ipv4()
    if not ip:
        print("[!] 拿不到本机 IP")
        return None
    prefix = ip.rsplit(".", 1)[0]
    print(f"[*] 在 {prefix}.0/24 上找 MAC {mac} ...")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for i in range(1, 255):
        try:
            s.sendto(b"\x00", (f"{prefix}.{i}", 9))
        except Exception:
            pass
    s.close()
    import time
    for _ in range(int(timeout / 2)):
        for host, m in arp_table().items():
            if m == mac:
                return host
        time.sleep(2)
    return None


def ps(cmd):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True, encoding="gbk", errors="ignore")
    return (r.stdout or "") + (r.stderr or "")


def sync_printer_port(printer_name, new_ip):
    """把 Windows 打印机的端口指向盒子的新 IP。

    注意：PrintManagement 模块没有 Set-PrinterPort（只有 Add/Get/Remove），
    所以改 IP 的正确做法是「建新端口 + Set-Printer 指向它 + 删旧端口」。
    """
    new_port = "IP_" + new_ip
    cur = ps(f"(Get-Printer -Name '{printer_name}' -ErrorAction SilentlyContinue).PortName").strip()
    print(f"[*] 打印机 {printer_name} 当前端口: {cur or '(未找到该打印机)'}")

    if cur == new_port:
        print("[*] 已经指向正确端口，无需更新")
    else:
        exists = ps(f"(Get-PrinterPort -Name '{new_port}' -ErrorAction SilentlyContinue).Name").strip()
        if not exists:
            print(ps(f"Add-PrinterPort -Name '{new_port}' -PrinterHostAddress '{new_ip}' -PortNumber 9100"))
            print(f"[+] 已创建端口 {new_port} -> {new_ip}:9100")
        print(ps(f"Set-Printer -Name '{printer_name}' -PortName '{new_port}'"))
        print(f"[+] 打印机已指向 {new_port}")
        # 清理旧的托管端口（IP_* 以及历史遗留的固定名）
        old = ps(
            "Get-PrinterPort | Where-Object { ($_.Name -like 'IP_*' -or $_.Name -eq 'XB900WB') "
            f"-and $_.Name -ne '{new_port}' }} | ForEach-Object {{ Remove-PrinterPort -Name $_.Name "
            "-ErrorAction SilentlyContinue; $_.Name }"
        ).strip()
        if old:
            print(f"[*] 已清理旧端口: {old}")

    # 验证 9100
    ok = False
    try:
        t = socket.create_connection((new_ip, 9100), timeout=3)
        t.close(); ok = True
    except Exception:
        ok = False
    print(f"[{'✓' if ok else '!'}] {new_ip}:9100 {'可达' if ok else '不可达'}")
    return new_ip


async def find_box(addr=None, timeout=15, scan_sec=30):
    if addr:
        dev = await BleakScanner.find_device_by_address(addr, timeout=timeout)
        if dev:
            return dev
        print("[!] 指定地址没找到，改为按名字扫描")
    found = {}

    def cb(d, adv):
        nm = d.name or adv.local_name or ""
        if any(nm.startswith(p) for p in NAME_PREFIXES):
            found[d.address] = d

    print(f"[*] 扫描蓝牙 {scan_sec} 秒，找盒子 ...")
    emit_prog(10, "扫描蓝牙 ...")
    async with BleakScanner(detection_callback=cb):
        await asyncio.sleep(scan_sec)
    if not found:
        return None
    lst = list(found.values())
    lst.sort(key=lambda d: getattr(d, "rssi", -999) or -999, reverse=True)
    print("[*] 找到:", ", ".join(f"{d.name}({d.address})" for d in lst))
    emit_prog(35, "已找到盒子，正在连接 ...")
    return lst[0]


async def ble_still_alive(scan_sec=10):
    """配网失败后探测盒子蓝牙是否还在线（决定能否不碰盒子自救）"""
    found = {}

    def cb(d, adv):
        nm = d.name or adv.local_name or ""
        if any(nm.startswith(p) for p in NAME_PREFIXES):
            found[d.address] = d

    try:
        async with BleakScanner(detection_callback=cb):
            await asyncio.sleep(scan_sec)
    except Exception:
        pass
    lst = list(found.values())
    return lst[0] if lst else None


async def do_list(dev):
    buf = []
    async with BleakClient(dev, timeout=25) as c:
        print("[+] 已连接:", c.is_connected)
        emit_prog(50, "已连接，读取盒子状态 ...")
        try:
            code = await c.read_gatt_char(CH_READ)
            print("[*] bindCode 读取:", bytes(code))
        except Exception as e:
            print("[!] 读 bindCode 失败:", type(e).__name__)

        done = asyncio.Event()

        def on_data(_ch, data):
            chunk = bytes(data).decode("utf-8", "ignore")
            buf.append(chunk)
            text = "".join(buf)
            if "#endsplit" in text:
                done.set()

        await c.start_notify(CH_NOTIFY, on_data)
        try:
            await c.start_notify(CH_NOTIFY2, on_data)
        except Exception:
            pass
        print("[*] 等待盒子推送 Wi-Fi 列表 ...")
        emit_prog(65, "等待盒子上报 Wi-Fi 列表 ...")
        try:
            await asyncio.wait_for(done.wait(), timeout=20)
        except asyncio.TimeoutError:
            print("[!] 超时，原始收到:")
            print(repr("".join(buf)))
            return
        raw = "".join(buf)
        inner = raw.split("#split#", 1)[1].split("#endsplit", 1)[0]
        names = [x for x in inner.split("||") if x.strip()]
        print("\n[+] 盒子扫到的 Wi-Fi 列表:")
        for i, n in enumerate(names, 1):
            print(f"    {i:2d}. {n}")
        print("\n[*] 原始载荷:", repr(raw))
        emit_prog(100, "完成")
        emit({"ok": True, "mode": "list", "name": dev.name, "addr": dev.address,
              "ssids": names})


async def do_set(dev, ssid, pwd, plus_space=False, delay=0.25, use_response=True,
                 urlenc=False, net_timeout=25):
    # 协议来自 BluetoothVm.v0():
    #   raw = SSID + "||" + PWD
    #   payload = Base64(raw)          <- 反编译确认 be2.f 就是 Base64.encodeToString
    #   full = payload + "#end"        <- 再按 18 字符切片逐段写入
    raw = ssid + "||" + pwd
    if urlenc:
        payload = urllib.parse.quote(raw, safe="")
    else:
        payload = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    full = payload + "#end"
    chunks = [full[i:i + CHUNK] for i in range(0, len(full), CHUNK)]

    print(f"[*] SSID     : {ssid}")
    print(f"[*] 原始: {raw}")
    print(f"[*] Base64 载荷: {full}   ({len(full)} 字符 -> {len(chunks)} 段)")
    emit_prog(35, "构造配网数据 ...")

    notes = []
    net = {"state": None}
    up = asyncio.Event()
    async with BleakClient(dev, timeout=25) as c:
        print("[+] 已连接:", c.is_connected)

        def on_note(_ch, data):
            t = bytes(data).decode("utf-8", "ignore")
            notes.append(t)
            print("    [应答] %s" % repr(t))
            if t in ("true", "false"):
                net["state"] = t
                if t == "true":
                    up.set()

        try:
            await c.start_notify(CH_NOTIFY, on_note)
        except Exception as e:
            print("[!] 订阅 notify 失败:", type(e).__name__)
        try:
            await c.start_notify(CH_NOTIFY2, on_note)
        except Exception:
            pass

        for i, ch in enumerate(chunks, 1):
            await c.write_gatt_char(CH_WRITE, ch.encode("utf-8"), response=use_response)
            print(f"    [{i}/{len(chunks)}] -> {ch}")
            emit_prog(35 + int(20 * i / len(chunks)), f"下发配网数据 {i}/{len(chunks)}")
            await asyncio.sleep(delay)

        print("[*] 已全部下发，等待盒子回应 12 秒 ...")
        emit_prog(60, "已下发，等待盒子联网状态 ...")
        try:
            await asyncio.wait_for(up.wait(), timeout=net_timeout)
            print("[+] 盒子已上报联网状态 true -> 配网成功")
            emit_prog(75, "盒子已联网")
        except asyncio.TimeoutError:
            print("[!] %d 秒内没有等到 true（盒子可能没连上）" % net_timeout)
            await asyncio.sleep(3)

    print("[*] 收到应答:", notes if notes else "(无)")
    print("[*] 结束。盒子上若成功会自行连接该 Wi-Fi。")
    emit_prog(80, "配网指令完成")
    result = {"net_up": net["state"] == "true", "state": net["state"],
              "replies": notes, "sent": len(chunks), "payload": full}
    emit({"ok": True, "mode": "set", "ssid": ssid, "sent": len(chunks),
          "payload": full, "net_up": result["net_up"], "state": net["state"],
          "replies": notes})
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只读取盒子扫到的 Wi-Fi 列表（安全）")
    ap.add_argument("--set", nargs=2, metavar=("SSID", "PASSWORD"), help="下发 Wi-Fi 配网")
    ap.add_argument("--set-open", metavar="SSID",
                    help="配网到开放网络(无密码)，等价于密码为空")
    ap.add_argument("--addr", help="盒子 BLE 地址，默认自动扫描")
    ap.add_argument("--plus-space", action="store_true", help="空格编码为 + 而不是 %%20")
    ap.add_argument("--urlenc", action="store_true", help="改用 URL 编码（旧猜测，默认已改为 Base64）")
    ap.add_argument("--delay", type=float, default=0.25, help="每段之间延时秒")
    ap.add_argument("--printer-name", default="Codex-9100-HP2300",
                    help="要同步的 Windows 打印机队列名")
    ap.add_argument("--no-sync", action="store_true", help="配网后不自动同步打印机端口")
    ap.add_argument("--only-sync", action="store_true", help="只做打印机端口同步(不配网)")
    ap.add_argument("--json", action="store_true", help="在输出末尾追加一行 JSON 结果（供 GUI 调用）")
    ap.add_argument("--scan-time", type=int, default=30, help="扫描蓝牙的秒数")
    ap.add_argument("--net-timeout", type=int, default=25, help="下发后等待盒子联网状态 true 的秒数")
    ap.add_argument("--json-out", help="把 JSON 结果写入该文件(UTF-8)")
    ap.add_argument("--log-out", help="把日志写入该文件(UTF-8)，供 GUI 读取，避免管道编码问题")
    args = ap.parse_args()

    global EMIT_JSON, JSON_OUT
    EMIT_JSON = args.json
    JSON_OUT = args.json_out

    if args.log_out:
        class _Tee:
            def __init__(self, path, orig):
                self.f = open(path, "w", encoding="utf-8")
                self.orig = orig

            def write(self, s):
                try:
                    self.orig.write(s)
                except Exception:
                    pass
                self.f.write(s)
                return len(s)

            def flush(self):
                try:
                    self.orig.flush()
                except Exception:
                    pass
                self.f.flush()

        sys.stdout = _Tee(args.log_out, sys.stdout)

    async def run():
        if args.only_sync:
            emit_prog(20, "查找盒子 IP ...")
            ip = find_ip_by_mac()
            if not ip:
                print("[!] 没在局域网找到盒子（按 MAC 匹配）")
                emit({"ok": False, "mode": "sync", "error": "box not found by MAC"})
                return 1
            print(f"[+] 盒子当前 IP: {ip}")
            sync_printer_port(args.printer_name, ip)
            emit_prog(100, "完成")
            emit({"ok": True, "mode": "sync", "ip": ip, "printer": args.printer_name})
            return 0

        dev = await find_box(args.addr, scan_sec=args.scan_time)
        if not dev:
            print("[!] 找不到盒子。确认盒子通电、在待配网状态、距离近。")
            emit({"ok": False, "mode": "scan", "error": "box not found"})
            return 1
        if args.set or args.set_open:
            if args.set_open:
                ssid, pwd = args.set_open, ""
                print("\n[*] 开放网络模式（密码为空）")
            else:
                ssid, pwd = args.set
            print("\n!! 注意：下发后盒子会断开当前 Wi-Fi 去连新的，密码错就要重新配网。")
            res = await do_set(dev, ssid, pwd, plus_space=args.plus_space, delay=args.delay,
                               urlenc=args.urlenc, net_timeout=args.net_timeout)
            if not args.no_sync:
                print("\n[*] 等待盒子接入新网络并同步打印机端口 ...")
                if res and res.get("net_up"):
                    emit_prog(85, "盒子已联网，查找 IP ...")
                else:
                    emit_prog(70, "等待盒子接入新网络 ...")
                    await asyncio.sleep(20)
                emit_prog(85, "查找盒子新 IP ...")
                ip = find_ip_by_mac()
                if ip:
                    print(f"[+] 盒子新 IP: {ip}")
                    emit_prog(92, "更新打印机端口 ...")
                    sync_printer_port(args.printer_name, ip)
                    emit_prog(100, "完成")
                    emit({"ok": True, "mode": "set+sync", "ip": ip, "printer": args.printer_name,
                          "net_up": bool(res and res.get("net_up"))})
                else:
                    print("[!] 没找到盒子新 IP —— 判定为【配网失败】")
                    print(f"    盒子联网状态上报: {res.get('state') if res else '未知'}")
                    print("    常见原因: 密码错误 / 该 Wi-Fi 盒子连不上 / 不在同一网段")
                    emit_prog(95, "配网可能失败，检查盒子蓝牙是否还在 ...")
                    alive = await ble_still_alive(10)
                    if alive:
                        print("[+] 盒子蓝牙仍在线 -> 可以直接再配一次，不用碰盒子")
                    else:
                        print("[!] 盒子蓝牙也不在了 -> 需要断电重启后再试")
                    print(f"    也可稍后手动重试: python ble_config.py --only-sync")
                    emit({"ok": False, "mode": "set+sync", "error": "new IP not found",
                          "ble_alive": bool(alive),
                          "net_state": (res.get("state") if res else None)})
        else:
            await do_list(dev)
        return 0

    sys.exit(asyncio.run(run()))


main()

