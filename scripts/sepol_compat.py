#!/usr/bin/env python3
"""
sepol_compat.py - sesuaikan CIL vendor/odm ke class & permission yang dikenal platform donor.

  sepol_compat.py PLAT_SEPOLICY_CIL VENDOR_CIL...

Class dan permission SELinux didefinisikan platform (plat_sepolicy.cil di system). Vendor yang
lebih baru dari system donor bisa memakai permission yang belum ada di sana, mis. vendor
LineageOS 23.2 (Android 16 QPR2) memakai `nlmsg` (netlink_route_socket) sementara system
OxygenOS 16 belum -> "Failed to resolve permission nlmsg" -> init gagal compile sepolicy ->
bootloop ke recovery. Di tiap file vendor/odm:

  (allow|auditallow|dontaudit|neverallow S T (CLASS (perm...)))
      permission yang tidak didefinisikan untuk CLASS (class + common) dibuang; kalau tidak ada
      yang tersisa / class tidak dikenal, baris dikomentari
  (allowx|auditallowx|dontauditx|neverallowx S T (KIND CLASS ...))
      dikomentari kalau KIND (ioctl / nlmsg) bukan permission CLASS di platform donor

Baris yang diubah ditandai "; [port-compat]". Kernel 5.10 ingres juga tidak mengenal permission
baru itu, jadi tidak ada izin nyata yang hilang. Cetak "<file>: n" dan "RESULT n".
"""
import re
import sys

TAG = "; [port-compat]"


def classes(plat):
    common, cls, cc = {}, {}, {}
    for line in open(plat, encoding="utf-8", errors="surrogateescape"):
        s = line.strip()
        m = re.match(r"^\(common (\S+) \(([^()]*)\)\)$", s)
        if m:
            common[m.group(1)] = set(m.group(2).split())
            continue
        m = re.match(r"^\(class (\S+) \(([^()]*)\)\)$", s)
        if m:
            cls.setdefault(m.group(1), set()).update(m.group(2).split())
            continue
        m = re.match(r"^\(classcommon (\S+) (\S+)\)$", s)
        if m:
            cc[m.group(1)] = m.group(2)
    for c, com in cc.items():
        cls.setdefault(c, set()).update(common.get(com, set()))
    return cls


AV = re.compile(r"^\((allow|auditallow|dontaudit|neverallow) (\S+) (\S+) \((\S+) \(([^()]*)\)\)\)(.*)$")
AVX = re.compile(r"^\((allowx|auditallowx|dontauditx|neverallowx) (\S+) (\S+) \((\S+) (\S+) ")


def fix_file(path, cls):
    lines = open(path, encoding="utf-8", errors="surrogateescape").read().split("\n")
    n = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if not s.startswith("(") or TAG in s:
            continue
        m = AV.match(s)
        if m:
            kind, src, tgt, c, perms, rest = m.groups()
            if c not in cls:
                lines[i] = ";" + s + " " + TAG + " class tidak dikenal"
                n += 1
                continue
            ps = perms.split()
            keep = [p for p in ps if p in cls[c] or p in ("*", "all")]
            if len(keep) == len(ps):
                continue
            n += 1
            if keep:
                lines[i] = "(%s %s %s (%s (%s)))%s %s" % (kind, src, tgt, c, " ".join(keep), rest, TAG)
            else:
                lines[i] = ";" + s + " " + TAG
            continue
        m = AVX.match(s)
        if m:
            perm, c = m.group(4), m.group(5)
            if c not in cls or perm not in cls[c]:
                lines[i] = ";" + s + " " + TAG
                n += 1
    if n:
        with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write("\n".join(lines))
    return n


def main(argv):
    if len(argv) < 3:
        raise SystemExit(__doc__)
    cls = classes(argv[1])
    if not cls:
        print("RESULT ? (class platform tidak terbaca)")
        return
    total = 0
    for p in argv[2:]:
        try:
            n = fix_file(p, cls)
        except OSError:
            continue
        if n:
            print("%s: %d" % (p, n))
        total += n
    print("RESULT %d" % total)


if __name__ == "__main__":
    main(sys.argv)
