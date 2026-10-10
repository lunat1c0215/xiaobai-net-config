import os, zipfile, logging

logging.disable(logging.CRITICAL)
from androguard.core.dex import DEX

APK = os.path.expanduser(r"~\AppData\Local\Temp\xbbox.apk")
TARGETS = [
    "Lcom/adprof/sdk/be2;",              # 编码函数 f()
    "Lcom/mango/ep300/viewmodel/BluetoothVm$c;",   # 写通道 UUID
    "Lcom/mango/ep300/viewmodel/BluetoothVm$b;",   # 读通道 UUID
]


def main():
    z = zipfile.ZipFile(APK)
    for n in [x for x in z.namelist() if x.endswith(".dex")]:
        data = z.read(n)
        if not any(t.encode() in data for t in TARGETS):
            continue
        d = DEX(data)
        for cls in d.get_classes():
            if cls.get_name() not in TARGETS:
                continue
            print("=" * 70)
            print("DEX:", n, "| CLASS:", cls.get_name())
            for m in cls.get_methods():
                nm = m.get_name()
                # be2 类很大，只看 f 方法；内部类全看
                if cls.get_name() == "Lcom/adprof/sdk/be2;" and nm not in ("f", "g", "e"):
                    continue
                print("\n--- %s%s" % (nm, m.get_descriptor()))
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
