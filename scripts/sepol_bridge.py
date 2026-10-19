#!/usr/bin/env python3
"""
sepol_bridge.py - jembatan sepolicy: type public system_ext/product ROM base yang dipakai vendor
ingres tapi tidak ada di ROM donor, diambil dari policy base dan ditambahkan ke system_ext donor.

Vendor merujuk type public platform lewat atribut ber-versi (mis. vendor_qtelephony_202504,
dideklarasikan plat_pub_versioned.cil). Isinya diisi file mapping system/system_ext/product
(typeattributeset X_202504 (X)). Kalau system_ext donor (mis. OxygenOS, AOSP murni) tidak punya
type X milik system_ext base (LineageOS / QTI), atribut itu kosong: compile tetap lolos, tapi
aturan vendor untuk X tidak berlaku -> HAL/layanan vendor ditolak SELinux saat jalan (telepon,
display, kamera, WFD, ...).

  sepol_bridge.py --ver 202504 \\
      --donor CIL...      plat_sepolicy.cil, mapping plat, system_ext/product donor (+ mapping)
      --have CIL...       vendor_sepolicy.cil, plat_pub_versioned.cil, odm_sepolicy.cil
      --use CIL...        vendor/odm (yang merujuk atribut ber-versi)
      --base CIL...       system_ext_sepolicy.cil / product_sepolicy.cil ROM base
      --base-map CIL...   mapping/<ver>.cil system_ext / product ROM base
      --tag TAG --out-cil FILE --out-map FILE
      [--ctx BASE_CONTEXTS=DONOR_CONTEXTS ...]   service/hwservice/property contexts

Yang ditambahkan (hanya bila semua nama dikenal; neverallow & atribut buatan compiler dibuang):
  type/typeattribute X + roletype + keanggotaan atribut, allow/allowx/dontaudit/typetransition yang
  menyebut X, baris mapping X_<ver>, baris contexts berlabel X yang key-nya belum ada di donor.
Cetak "GRAFT <type>" per type dan "RESULT <atribut ber-versi> <type> <aturan> <contexts>".
"""
import argparse
import os
import re
import sys

DECL = re.compile(r"^\((type|typeattribute|typealias|attribute)\s+([^\s()]+)\)\s*$")
NAME = re.compile(r"[A-Za-z0-9_.-]+")


def read(p):
    try:
        return open(p, encoding="utf-8", errors="surrogateescape").read()
    except OSError:
        return ""


def decls(files):
    out = set()
    for f in files:
        for line in read(f).splitlines():
            m = DECL.match(line.strip())
            if m:
                out.add(m.group(2))
    return out


def mapsets(files, ver):
    """{X_ver: [members]} dan baris expandtypeattribute per X_ver"""
    sets, expand = {}, {}
    rx = re.compile(r"^\(typeattributeset (\S+_%s) \(([^()]*)\)\)\s*$" % re.escape(ver))
    rxe = re.compile(r"^\(expandtypeattribute \((\S+_%s)\) (true|false)\)\s*$" % re.escape(ver))
    for f in files:
        for line in read(f).splitlines():
            s = line.strip()
            m = rx.match(s)
            if m:
                sets.setdefault(m.group(1), []).extend(m.group(2).split())
                continue
            m = rxe.match(s)
            if m:
                expand[m.group(1)] = s
    return sets, expand


def main(argv):
    ap = argparse.ArgumentParser(prog="sepol_bridge.py")
    ap.add_argument("--ver", required=True)
    for k in ("donor", "have", "use", "base", "base-map", "ctx"):
        ap.add_argument("--" + k, nargs="*", default=[])
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out-cil", required=True)
    ap.add_argument("--out-map", required=True)
    a = ap.parse_args(argv)
    ver = a.ver

    donor_decl = decls(a.donor) | decls(a.have)
    donor_map, _ = mapsets(a.donor, ver)
    base_map, base_expand = mapsets(a.base_map, ver)

    used = set()
    vrx = re.compile(r"[A-Za-z0-9_.-]+_%s\b" % re.escape(ver))
    for f in a.use:
        used |= set(vrx.findall(read(f)))
    need = sorted(x for x in used if x not in donor_map and x in base_map)

    base_decl = {}
    base_lines = []
    for f in a.base:
        for line in read(f).splitlines():
            s = line.strip()
            if not s or s.startswith(";"):
                continue
            base_lines.append(s)
            m = DECL.match(s)
            if m:
                base_decl.setdefault(m.group(2), s)

    graft = set()      # type yang deklarasinya ikut diambil dari base
    bridged = set()    # semua type di balik atribut ber-versi yang dijembatani
    for x in need:
        for t in base_map[x]:
            if t not in donor_decl and t in base_decl:
                graft.add(t)
            if t in donor_decl or t in base_decl:
                bridged.add(t)
    known = donor_decl | graft | {"self"}

    out_cil, rules = [], 0
    for t in sorted(graft):
        out_cil.append(base_decl[t])
    for s in base_lines:
        if s.startswith(("(neverallow", "(type ", "(typeattribute ", "(typealias ", "(attribute ")) \
                or "base_typeattr_" in s:
            continue
        m = re.match(r"^\(expandtypeattribute \(([^()\s]+)\) (true|false)\)$", s)
        if m:
            if m.group(1) in graft:
                out_cil.append(s)
            continue
        m = re.match(r"^\(roletype object_r ([^()\s]+)\)$", s)
        if m:
            if m.group(1) in graft:
                out_cil.append(s)
            continue
        m = re.match(r"^\(typeattributeset ([^()\s]+) \(([^()]*)\)\)$", s)
        if m:
            attr, mem = m.group(1), m.group(2).split()
            if attr in graft:
                keep = [t for t in mem if t in known]
            elif attr in known and attr not in ("domain", "file_type", "fs_type", "dev_type", "exec_type"):
                keep = [t for t in mem if t in graft]
            else:
                keep = []
            if keep:
                out_cil.append("(typeattributeset %s (%s))" % (attr, " ".join(keep)))
            continue
        m = re.match(r"^\((allow|allowx|dontaudit|auditallow|typetransition) ([^()\s]+) ([^()\s]+) ", s)
        if not m:
            continue
        names = [m.group(2), m.group(3)]
        if m.group(1) == "typetransition":
            names += re.findall(r"([^()\s]+)\)$", s)
        if not any(n in bridged for n in names) or any(n not in known for n in names):
            continue
        out_cil.append(s)
        rules += 1

    out_map = []
    for x in need:
        mem = [t for t in base_map[x] if t in known]
        if not mem:
            continue
        out_map.append("(typeattributeset %s (%s))" % (x, " ".join(mem)))
        if x in base_expand:
            out_map.append(base_expand[x])

    with open(a.out_cil, "w", encoding="utf-8") as f:
        for s in out_cil:
            f.write("%s %s\n" % (s, a.tag))
    with open(a.out_map, "w", encoding="utf-8") as f:
        for s in out_map:
            f.write("%s %s\n" % (s, a.tag))

    nctx = 0
    for pair in a.ctx:
        base_f, _, donor_f = pair.partition("=")
        have_keys = set()
        for line in read(donor_f).splitlines():
            p = line.split()
            if p and not p[0].startswith("#"):
                have_keys.add(p[0])
        add = []
        for line in read(base_f).splitlines():
            p = line.split()
            if len(p) < 2 or p[0].startswith("#") or p[0] in have_keys:
                continue
            lab = p[-1].split(":")
            if len(lab) >= 3 and lab[2] in bridged:
                add.append(line.rstrip())
                have_keys.add(p[0])
        if add:
            txt = read(donor_f)
            os.makedirs(os.path.dirname(donor_f), exist_ok=True)
            with open(donor_f, "a", encoding="utf-8") as f:
                if txt and not txt.endswith("\n"):
                    f.write("\n")
                f.write("\n".join(add) + "\n")
            nctx += len(add)
            print("CTX %s +%d" % (donor_f, len(add)))

    for t in sorted(graft):
        print("GRAFT %s" % t)
    print("RESULT %d %d %d %d" % (len([x for x in out_map if x.startswith("(typeattributeset")]),
                                  len(graft), rules, nctx))


if __name__ == "__main__":
    main(sys.argv[1:])
