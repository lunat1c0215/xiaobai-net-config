import os, sys, zipfile
from androguard.core.dex import DEX

APK = os.path.expanduser(r"~\AppData\Local\Temp\xbbox.apk")
TARGET = "Lcom/mango/ep300/viewmodel/BluetoothVm;"


def interesting(s):
    return s


def main():
    z = zipfile.ZipFile(APK)
    dexes = [n for n in z.namelist() if n.endswith(".dex")]
    for n in dexes:
        data = z.read(n)
        if TARGET.encode() not in data:
            continue
        print("=" * 70)
        print("DEX:", n)
        d = DEX(data)
        for cls in d.get_classes():
            if cls.get_name() != TARGET:
                continue
            print("CLASS:", cls.get_name(), "super:", cls.get_superclassname())
            for m in cls.get_methods():
                name = m.get_name()
                print(f"\n  METHOD {name}{m.get_descriptor()}")
                code = None
                try:
                    code = m.get_code()
                except Exception:
                    pass
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
                        print(f"     {ins.get_name():20s} {out}")


main()
