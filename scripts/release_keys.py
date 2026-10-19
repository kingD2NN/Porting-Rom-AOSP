#!/usr/bin/env python3
"""
release_keys.py - samarkan build ROM port sebagai user/release-keys.

  release_keys.py [--keep-debug] FILE...      (build.prop / default.prop / prop.default)

--keep-debug: ro.debuggable / ro.secure / ro.adb.secure tidak diubah (dipakai saat DEBUG_ADB=true)

ROM donor sering di-build userdebug / eng dengan test-keys / dev-keys. Aplikasi bank, e-wallet
(DANA dll), pemeriksa root sederhana dan Play Integrity (MEETS_BASIC_INTEGRITY) menolak build
seperti itu. Yang diubah di tiap file (hanya key yang sudah ada; nilai lain tidak disentuh):

  ro.*build.type                      -> user          (dari userdebug / eng)
  ro.*build.tags, ro.*build.keys      -> release-keys  (dari test-keys / dev-keys)
  ro.*build.fingerprint / thumbprint  ":userdebug/" | ":eng/" -> ":user/", test|dev-keys -> release-keys
  ro.*build.description / flavor /
  ro.build.display.id                 "-userdebug" | "-eng" -> "-user", test|dev-keys -> release-keys
  ro.debuggable, ro.*.debuggable      -> 0
  ro.secure, ro.adb.secure            -> 1

ro.boot.* (verifiedbootstate, flash.locked, vbmeta.device_state) TIDAK bisa diubah lewat
build.prop: nilainya dari bootloader dan sudah terkunci sebelum build.prop dimuat.
Mencetak "<file>: n" per file dan "TOTAL n".
"""
import re
import sys

LINE = re.compile(r"^(\s*)([^#=\s][^=]*?)(\s*=\s*)(.*?)(\s*)$")
STR_KEYS = re.compile(r"(\.build\.(fingerprint|thumbprint|description|flavor)|^ro\.build\.display\.id)$")


KEEP_DEBUG = False


def fix(key, val):
    if key.endswith(".build.type") or key == "ro.build.type":
        return "user" if val in ("userdebug", "eng") else val
    if key.endswith((".build.tags", ".build.keys")):
        return "release-keys" if val in ("test-keys", "dev-keys") else val
    if KEEP_DEBUG and (key.endswith("debuggable") or key in ("ro.secure", "ro.adb.secure")):
        return val
    if key == "ro.debuggable" or key.endswith(".debuggable"):
        return "0" if val == "1" else val
    if key in ("ro.secure", "ro.adb.secure"):
        return "1" if val == "0" else val
    if STR_KEYS.search(key):
        v = val.replace(":userdebug/", ":user/").replace(":eng/", ":user/")
        v = re.sub(r"-(userdebug|eng)(?=$|\s)", "-user", v)
        v = re.sub(r"(?<![A-Za-z])(test|dev)-keys\b", "release-keys", v)
        return v
    return val


def process(path):
    try:
        lines = open(path, encoding="utf-8", errors="surrogateescape").read().split("\n")
    except OSError:
        return 0
    n = 0
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("#", "import ")):
            continue
        m = LINE.match(line)
        if not m:
            continue
        key, val = m.group(2), m.group(4)
        new = fix(key, val)
        if new != val:
            lines[i] = m.group(1) + key + m.group(3) + new + m.group(5)
            n += 1
    if n:
        with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write("\n".join(lines))
    return n


def main(argv):
    global KEEP_DEBUG
    args = argv[1:]
    if args and args[0] == "--keep-debug":
        KEEP_DEBUG = True
        args = args[1:]
    if not args:
        raise SystemExit(__doc__)
    total = 0
    for p in args:
        n = process(p)
        if n:
            print("%s: %d" % (p, n))
        total += n
    print("TOTAL %d" % total)


if __name__ == "__main__":
    main(sys.argv)
