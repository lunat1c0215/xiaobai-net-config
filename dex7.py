import os, zipfile, logging

logging.disable(logging.CRITICAL)
from androguard.core.dex import DEX

APK = os.path.expanduser(r"~\AppData\Local\Temp\xbbox.apk")
SIG = "Z5(Ljava/lang/CharSequence;)Ljava/lang/CharSequence;"


def main():
    z = zipfile.ZipFile(APK)
    for n in [x for x in z.namelist() if x.endswith(".dex")]:
        data = z.read(n)
        if SIG.encode() not in data:
            continue
        d = DEX(data)
        for cls in d.get_classes():
            if "StringsKt" not in cls.get_name():
                continue
            for m in cls.get_methods():
                if m.get_name() != "Z5":
                    continue
                print("=" * 60)
                print("DEX:", n, "| CLASS:", cls.get_name(), "| METHOD Z5")
                try:
                    code = m.get_code()
                except Exception:
                    code = None
                if not code:
                    print("  (no code)")
                    continue
                for ins in code.get_bc().get_instructions():
                    try:
                        out = ins.get_output()
                    except Exception:
                        out = ""
                    if out:
                        print("   %-18s %s" % (ins.get_name(), out))


main()
