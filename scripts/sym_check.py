#!/usr/bin/env python3
"""
sym_check.py - cek simbol: setiap simbol undefined (import) library yang akan dipasang harus
diekspor oleh library di pohon DT_NEEDED-nya (cara linker Android: semua simbol di-bind saat
dlopen, simbol yang tidak ada = "cannot locate symbol" -> app/daemon crash, untuk driver GPU
= surfaceflinger crash = bootloop).

  sym_check.py --check PATH [--check PATH ...] --search DIR [--search DIR ...] [--label NAMA]

  --check  : file .so / folder (semua ELF di dalamnya, rekursif) yang dicek
  --search : folder tempat mencari library DT_NEEDED, urutan = prioritas (tidak rekursif;
             berikan lib64, lib64/egl, lib64/hw dst. secara eksplisit). Folder --check selalu
             dicari lebih dulu.

Aturan (supaya tidak ada vonis palsu):
  - import bertanda versi LIBC* (bionic) / simbol weak dilewati
  - library bionic (libc/libm/libdl/...) dan library yang tidak ketemu (mis. di APEX) tidak
    bisa dibaca ekspornya. Simbol C++ (_Z...) tetap divonis HILANG kalau tidak ada di library
    lain, karena bionic & library C-only (libnativehelper) tidak mengekspor simbol C++ ABI
    platform (libgui/libui/libbinder/...). Simbol C yang tidak ketemu divonis HILANG hanya
    kalau seluruh pohon NEEDED terbaca, selain itu "tidak bisa dipastikan" (info).
Exit: 0 = tidak ada simbol hilang, 1 = ada simbol hilang, 2 = error pemakaian.
Butuh: readelf (binutils). c++filt opsional (nama simbol lebih mudah dibaca).
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

BIONIC = {"libc.so", "libm.so", "libdl.so", "libdl_android.so", "ld-android.so"}
# library di APEX yang hanya mengekspor API C (extern "C")
C_ONLY = {"libnativehelper.so", "libnativeloader.so", "libandroidicu.so", "libvndksupport.so",
          "libneuralnetworks.so", "libbinder_ndk.so", "libmediandk.so", "libcamera2ndk.so"}
NEWDEL = re.compile(r"^_Z(nw|na|dl|da)[mjPSRv]")   # operator new/delete (bionic/libc++)
SYM = re.compile(r"^\s*\d+:\s+[0-9a-fA-F]+\s+(?:\d+|0x[0-9a-fA-F]+)\s+(\w+)\s+(\w+)\s+(\w+)"
                 r"(?:\s+\[[^\]]*\])?\s+(\w+)\s+(\S.*)?$")

_cache = {}


def elf_class(path):
    try:
        with open(path, "rb") as f:
            h = f.read(5)
    except OSError:
        return 0
    if len(h) < 5 or h[:4] != b"\x7fELF":
        return 0
    return 64 if h[4] == 2 else 32


def readelf(path):
    """-> (needed[], exports set, imports [(name, version)])"""
    if path in _cache:
        return _cache[path]
    needed, exports, imports = [], set(), []
    try:
        d = subprocess.run(["readelf", "-dW", path], capture_output=True, text=True, errors="replace").stdout
        needed = re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", d)
        s = subprocess.run(["readelf", "--dyn-syms", "-W", path], capture_output=True, text=True,
                           errors="replace").stdout
    except OSError:
        s = ""
    for line in s.splitlines():
        m = SYM.match(line)
        if not m:
            continue
        typ, bind, _vis, ndx, name = m.groups()
        if not name:
            continue
        name = name.strip()
        ver = ""
        if "@" in name:
            name, _, ver = name.partition("@")
            ver = ver.lstrip("@").split(" ")[0]
        if not name or typ in ("SECTION", "FILE"):
            continue
        if ndx == "UND":
            if bind == "GLOBAL":
                imports.append((name, ver))
        elif bind in ("GLOBAL", "WEAK", "UNIQUE", "GNU_UNIQUE"):
            exports.add(name)
    _cache[path] = (needed, exports, imports)
    return _cache[path]


def index_dirs(dirs, bits):
    idx = {}
    for d in dirs:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            p = os.path.join(d, n)
            if n in idx or not n.endswith(".so") or not os.path.isfile(p):
                continue
            if elf_class(p) == bits:
                idx[n] = p
    return idx


def demangle(names):
    if not names or not shutil.which("c++filt"):
        return {n: n for n in names}
    out = subprocess.run(["c++filt"], input="\n".join(names), capture_output=True, text=True).stdout.splitlines()
    return {n: (o if o else n) for n, o in zip(names, out + [""] * len(names))}


def check_one(path, idx):
    needed, _, imports = readelf(path)
    seen, order, unknown, queue = set(), [], set(), list(needed)
    has_bionic = False
    while queue:
        n = queue.pop(0)
        if n in seen:
            continue
        seen.add(n)
        if n in BIONIC:
            has_bionic = True
            continue
        p = idx.get(n)
        if not p:
            unknown.add(n)
            continue
        order.append(p)
        queue.extend(readelf(p)[0])
    avail = set()
    for p in order:
        avail |= readelf(p)[1]
    missing, unsure = [], []
    for name, ver in imports:
        if ver.startswith("LIBC"):
            continue
        if name in avail:
            continue
        if name.startswith("_Z") and not NEWDEL.match(name):
            if unknown - C_ONLY:
                unsure.append(name)
            else:
                missing.append(name)
        elif unknown or has_bionic:
            unsure.append(name)
        else:
            missing.append(name)
    return sorted(set(missing)), sorted(set(unsure)), sorted(unknown)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="append", required=True)
    ap.add_argument("--search", action="append", default=[])
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    if not shutil.which("readelf"):
        print("sym_check: readelf tidak ada, cek simbol dilewati")
        return 0
    targets, check_dirs = [], []
    for c in a.check:
        if os.path.isdir(c):
            for root, dirs, files in os.walk(c):
                dirs.sort()
                check_dirs.append(root)
                for f in sorted(files):
                    p = os.path.join(root, f)
                    if f.endswith(".so") and not os.path.islink(p) and elf_class(p):
                        targets.append(p)
        elif elf_class(c):
            targets.append(c)
            check_dirs.append(os.path.dirname(c))
    if not targets:
        print("sym_check: tidak ada ELF untuk dicek")
        return 0
    idxs = {b: index_dirs(check_dirs + a.search, b) for b in (32, 64)}
    bad = 0
    for t in targets:
        missing, unsure, unknown = check_one(t, idxs[elf_class(t)])
        rel = os.path.basename(t)
        for c in a.check:
            if os.path.isdir(c) and t.startswith(c.rstrip("/") + "/"):
                rel = os.path.relpath(t, c)
        if missing:
            bad += 1
            dm = demangle(missing)
            print("HILANG %s: %d simbol tidak diekspor library mana pun di pohon NEEDED-nya" % (rel, len(missing)))
            for s in missing[:25]:
                print("    %s" % dm[s])
            if len(missing) > 25:
                print("    ... (+%d)" % (len(missing) - 25))
        elif unsure:
            print("ok     %s (%d simbol tidak bisa dipastikan: library %s tidak terbaca)"
                  % (rel, len(unsure), ", ".join(sorted(unknown)) or "bionic"))
        else:
            print("ok     %s" % rel)
    print("%s%d/%d library: semua simbol C++ tersedia" % (a.label + ": " if a.label else "", len(targets) - bad, len(targets))
          if not bad else "%s%d library punya simbol hilang -> akan crash saat dimuat (cannot locate symbol)"
          % (a.label + ": " if a.label else "", bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
