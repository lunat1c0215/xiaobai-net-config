import asyncio, sys
from bleak import BleakClient, BleakScanner

ADDR = "60:DE:F4:4E:42:57"
N1 = "1c927b50-c500-91e3-8a33-0800200c9a66"
N2 = "1c927b50-c500-11e3-8a33-0800200c9a66"
W  = "16fe0d80-c500-11e3-b8c8-0002a5d5c51b"
R  = "7c927b58-c500-91e3-9a32-2800201c9a6c"


async def main():
    dev = await BleakScanner.find_device_by_address(ADDR, timeout=15)
    if not dev:
        print("找不到设备（可能已不在广播态）")
        return
    got = []
    async with BleakClient(dev, timeout=25) as c:
        print("connected:", c.is_connected)
        try:
            v = await c.read_gatt_char(R)
            print("read R =", v, "hex:", v.hex())
        except Exception as e:
            print("read R err:", type(e).__name__, e)

        def mk(tag):
            def cb(ch, data):
                print(f"  [NOTIFY {tag}] {data!r}  hex={data.hex()}")
                got.append((tag, bytes(data)))
            return cb

        for u, t in ((N1, "svc1"), (N2, "svc2")):
            try:
                await c.start_notify(u, mk(t))
                print("subscribed", t, u)
            except Exception as e:
                print("sub err", t, type(e).__name__, e)

        print("等待 12 秒，看盒子是否主动推数据...")
        await asyncio.sleep(12)
        print("收到", len(got), "条通知")
        for u in (N1, N2):
            try:
                await c.stop_notify(u)
            except Exception:
                pass


asyncio.run(main())
