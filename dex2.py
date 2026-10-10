import os, zipfile, logging

logging.disable(logging.CRITICAL)
logging.getLogger("androguard").setLevel(logging.CRITICAL)

from androguard.core.dex import DEX

APK = os.path.expanduser(r"~\AppData\Local\Temp\xbbox.apk")
TARGET = "Lcom/mango/ep300/viewmodel/BluetoothVm;"

WANT = {
    "sendWifiData", "loadWifiData", "resolveNotifyData", "enableNotifyWifiData",
    "detectWifiData", "bindDevice", "dealBindDeviceSuccess", "getPairedNetState",
    "d0", "l0", "n0", "o0", "p0", "q0", "r0", "s0", "t0",
}


def main():
    z = zipfile.ZipFile(APK)
    for n in [x for x in z.namelist() if x.endswith(".dex")]:
        data = z.read(n)
        if TARGET.encode() not in data:
            continue
        d = DEX(data)
        for cls in d.get_classes():
            if cls.get_name() != TARGET:
                continue
            print("=" * 70)
            print("DEX:", n, "\nCLASS:", cls.get_name())
            for m in cls.get_methods():
                name = m.get_name()
                if name not in WANT:
                    continue
                print("\n--- METHOD %s%s" % (name, m.get_descriptor()))
                try:
                    code = m.get_code()
                except Exception:
                    code = None
                if not code:
                    print("     (no code)")
                    continue
                try:
                    bc = code.get_bc()
                except Exception:
                    print("     (no bc)")
                    continue
                for ins in bc.get_instructions():
                    try:
                        out = ins.get_output()
                    except Exception:
                        out = ""
                    if out:
                        print("     %-18s %s" % (ins.get_name(), out))


main()
