import os, sys, zipfile, logging

logging.disable(logging.CRITICAL)
from androguard.core.dex import DEX

APK = os.path.expanduser(r"~\AppData\Local\Temp\xbbox.apk")
TARGETS = [
    "Lcom/mango/ep300/fragment/Ep300SetPwdFrag;",
    "Lcom/mango/ep300/viewmodel/BluetoothVm$c;",
    "Lcom/mango/ep300/viewmodel/BluetoothVm$a;",
]


def main():
    z = zipfile.ZipFile(APK)
    dexes = [x for x in z.namelist() if x.endswith(".dex")]
    for n in dexes:
        data = z.read(n)
        if not any(t.encode() in data for t in TARGETS):
            continue
        d = DEX(data)
        for cls in d.get_classes():
            if cls.get_name() not in TARGETS:
                continue
            print("=" * 72)
            print("DEX:", n, "| CLASS:", cls.get_name())
            for m in cls.get_methods():
                print("\n--- %s%s" % (m.get_name(), m.get_descriptor()))
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
