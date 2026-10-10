"""监视盒子的 BLE 通知：联网状态 true/false、Wi-Fi 列表，带时间戳。"""
import asyncio
import datetime
from bleak import BleakClient, BleakScanner

ADDR = "60:DE:F4:4E:42:57"
N1 = "1c927b50-c500-91e3-8a33-0800200c9a66"
N2 = "1c927b50-c500-11e3-8a33-0800200c9a66"
SECONDS = 45


def ts():
    return datetime.datetime.now().strftime("%H:%M:%S")


async def main():
    dev = await BleakScanner.find_device_by_address(ADDR, timeout=20)
    if not dev:
        print(ts(), "找不到盒子（不在广播）")
        return
    buf = []
    last = None
    async with BleakClient(dev, timeout=25) as c:
        print(ts(), "已连接", c.is_connected)

        def on(_ch, data):
            nonlocal last
            t = bytes(data).decode("utf-8", "ignore")
            if t in ("true", "false"):
                if t != last:
                    print(ts(), "联网状态 ->", t)
                    last = t
                return
            buf.append(t)
            s = "".join(buf)
            if "#endsplit" in s:
                inner = s.split("#split#", 1)[1].split("#endsplit", 1)[0]
                print(ts(), "Wi-Fi 列表 ->", " | ".join(x for x in inner.split("||") if x))
                buf.clear()

        for u in (N1, N2):
            try:
                await c.start_notify(u, on)
            except Exception:
                pass
        print(ts(), f"监视 {SECONDS} 秒 ...")
        await asyncio.sleep(SECONDS)
        print(ts(), "结束")


asyncio.run(main())
