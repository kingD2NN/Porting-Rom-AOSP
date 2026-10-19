#!/usr/bin/env python3
"""
stock_fingerprint.py - pakai fingerprint firmware stok ingres untuk ROM port (seperti ROM asli).

  stock_fingerprint.py FINGERPRINT --set FILE --files FILE...

Settings > Tentang ponsel > "Sidik jari" (Build.FINGERPRINT) membaca ro.build.fingerprint, yang
di ROM port berisi fingerprint ROM donor (mis. POCO/marble_global/marble:15/... milik HP lain).
Di tiap FILE (build.prop system / system_ext / product / my_* donor), nilai key fingerprint
non-hardware diganti FINGERPRINT:

  ro.build.fingerprint, ro.system.build.fingerprint, ro.system_ext.build.fingerprint,
  ro.product.build.fingerprint, ro.system_dlkm.build.fingerprint, ro.<my_*>.build.fingerprint
  ro.build.description (+ ro.*.build.description) -> "<name>-<type> <release> <id> <incremental> <tags>"

ro.vendor.* / ro.odm.* / ro.bootimage.* tidak disentuh (sudah dari firmware ingres). --set: file
build.prop yang dimuat init PALING AKHIR (product): ro.build.fingerprint & ro.build.description
ditulis/ditambah di sana supaya menang atas nilai partisi lain.
Cetak "<file>: n" dan "TOTAL n".
"""
import re
import sys

LINE = re.compile(r"^(\s*)([^#=\s][^=]*?)(\s*=\s*)(.*?)(\s*)$")
FP_RE = re.compile(r"^([^/:\s]+)/([^/:\s]+)/([^/:\s]+):([^/:\s]+)/([^/:\s]+)/([^/:\s]+):([^/:\s]+)/([^/:\s]+)$")
SKIP = ("ro.vendor.", "ro.odm.", "ro.bootimage.", "ro.vendor_dlkm.", "ro.odm_dlkm.")


def description(fp):
    m = FP_RE.match(fp)
    if not m:
        return None
    _brand, name, _dev, rel, bid, inc, btype, tags = m.groups()
    return "%s-%s %s %s %s %s" % (name, btype, rel, bid, inc, tags)


def want(key):
    if key.startswith(SKIP):
        return None
    if key == "ro.build.fingerprint" or re.match(r"^ro\.[a-z0-9_]+\.build\.fingerprint$", key):
        return "fp"
    if key == "ro.build.description" or re.match(r"^ro\.[a-z0-9_]+\.build\.description$", key):
        return "desc"
    return None


def process(path, fp, desc, ensure=False):
    try:
        text = open(path, encoding="utf-8", errors="surrogateescape").read()
    except OSError:
        return 0
    lines = text.split("\n")
    n = 0
    seen = set()
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("#", "import ")):
            continue
        m = LINE.match(line)
        if not m:
            continue
        key, val = m.group(2), m.group(4)
        kind = want(key)
        if not kind:
            continue
        new = fp if kind == "fp" else desc
        seen.add(key)
        if new and new != val:
            lines[i] = m.group(1) + key + m.group(3) + new + m.group(5)
            n += 1
    if ensure:
        add = [("ro.build.fingerprint", fp)] + ([("ro.build.description", desc)] if desc else [])
        add = [(k, v) for k, v in add if k not in seen]
        if add:
            while lines and lines[-1] == "":
                lines.pop()
            lines.append("# [port] fingerprint firmware stok (Settings > Tentang ponsel > Sidik jari)")
            lines += ["%s=%s" % kv for kv in add]
            lines.append("")
            n += len(add)
    if n:
        with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write("\n".join(lines))
    return n


def main(argv):
    if len(argv) < 2 or not FP_RE.match(argv[1]):
        raise SystemExit("fingerprint tidak valid (format brand/name/device:release/id/incremental:type/tags)")
    fp = argv[1]
    desc = description(fp)
    sets, files, cur = [], [], None
    for a in argv[2:]:
        if a in ("--set", "--files"):
            cur = a
        elif cur == "--set":
            sets.append(a)
        elif cur == "--files":
            files.append(a)
    total = 0
    for p in dict.fromkeys(files + sets):
        n = process(p, fp, desc, ensure=p in sets)
        if n:
            print("%s: %d" % (p, n))
        total += n
    print("TOTAL %d" % total)


if __name__ == "__main__":
    main(sys.argv)
