import zipfile,os,re
p=os.path.expandvars(r"%TEMP%\xbbox.apk")
z=zipfile.ZipFile(p)
b=z.read('classes4.dex')
print("== classes4.dex 中含 ssid/pwd/wifi 的字符串 (长度<60) ==")
S=set()
for m in re.finditer(rb'[\x20-\x7e]{3,60}', b): S.add(m.group(0).decode('latin1'))
for s in sorted(S):
    if re.search(r'(?i)(ssid|pwd|passw|wifi)', s) and len(s)<60:
        print("   ",repr(s))
