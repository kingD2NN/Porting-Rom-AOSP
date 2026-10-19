#!/usr/bin/env python3
"""
oplus_tool.py - helper port donor OxygenOS / ColorOS / realme UI (OPlus) untuk port.sh.

  oplus_tool.py merge-config <root> <part>
      Gabung config/<part>_fs_config & _file_contexts (hasil extract.erofs / imgextractor, prefix
      "<part>/" dan "/<part>/") ke config/system_* sebagai folder di root image system
      ("system/<part>/..." dan "/system/<part>/..."). Entri lama system/<part> (titik mount /
      symlink) diganti. Label SELinux asli tiap file dipertahankan.

  oplus_tool.py filter-props --base FILE... --files FILE... [--drop-re REGEX]
      Di build.prop my_*: key yang juga diisi vendor/odm base (props hardware Xiaomi) dan key yang
      cocok --drop-re dikomentari ("#[port] key=..."), supaya nilai base tetap berlaku.

  oplus_tool.py odm-props <out> --donor FILE... --base FILE... [--skip-re REGEX]
      Ambil props OPlus (key memuat "oplus", ro.separate.soft, ro.build.device_family,
      ro.commonsoft.*) dari build.prop odm donor yang tidak menyangkut hardware (--skip-re) dan
      belum diisi base -> <out>.

  oplus_tool.py merge-ids <base passwd/group> <donor passwd/group>
      Tambah entri donor yang nama DAN id-nya belum dipakai base (entri Xiaomi tidak diubah).

  oplus_tool.py identity <root>
      "<nama ROM> <versi>" dari props my_* (mis. "OxygenOS 16.0.0.400_EX01").

  oplus_tool.py label-check <root>
      Type SELinux label file /system/my_* yang tidak dideklarasikan sepolicy system/system_ext/
      product donor (file berlabel type tak dikenal = "unlabeled", app/config tidak terbaca).
"""
import glob
import os
import re
import sys

ANY_RE = re.compile(r"^([^#=\s][^=]*?)\s*=(.*)$")


def read_props(path):
    out = {}
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as f:
            for line in f:
                m = ANY_RE.match(line.strip())
                if m and not line.lstrip().startswith("import "):
                    out[m.group(1).strip()] = m.group(2).strip()
    except OSError:
        pass
    return out


# ------------------------------------------------------------------ merge-config
def merge_config(root, part):
    cfg = os.path.join(root, "config")
    src_fs = os.path.join(cfg, part + "_fs_config")
    src_fc = os.path.join(cfg, part + "_file_contexts")
    dst_fs = os.path.join(cfg, "system_fs_config")
    dst_fc = os.path.join(cfg, "system_file_contexts")
    for p in (src_fs, dst_fs):
        if not os.path.isfile(p):
            raise SystemExit("tidak ada " + p)

    def keep_sys(path_key, prefix):
        # buang entri lama system/<part> dan isinya (titik mount kosong / symlink)
        return not (path_key == prefix or path_key.startswith(prefix + "/"))

    # ---- fs_config
    sys_lines = open(dst_fs, encoding="utf-8", errors="surrogateescape").read().splitlines()
    pre = "system/" + part
    out = [l for l in sys_lines if keep_sys(l.split(" ", 1)[0].rstrip("/"), pre)]
    root_mode = None
    added = 0
    for line in open(src_fs, encoding="utf-8", errors="surrogateescape").read().splitlines():
        if not line.strip():
            continue
        path, rest = (line.split(" ", 1) + [""])[:2]
        if path == "/":
            continue
        rel = path.rstrip("/")
        if rel == part:
            root_mode = rest
            continue
        if not rel.startswith(part + "/"):
            continue
        if rel == part + "/lost+found" or rel.startswith(part + "/lost+found/"):
            continue
        out.append("system/" + rel + " " + rest)
        added += 1
    out.append(pre + " " + (root_mode or "0 0 0755"))
    with open(dst_fs, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write("\n".join(out) + "\n")

    # ---- file_contexts
    if os.path.isfile(src_fc) and os.path.isfile(dst_fc):
        fpre = "/system/" + part
        sys_fc = open(dst_fc, encoding="utf-8", errors="surrogateescape").read().splitlines()
        outc = [l for l in sys_fc if keep_sys(l.split(" ", 1)[0].rstrip("/"), fpre)]
        root_label = None
        for line in open(src_fc, encoding="utf-8", errors="surrogateescape").read().splitlines():
            if not line.strip():
                continue
            path, rest = (line.split(" ", 1) + [""])[:2]
            p = path.rstrip("/")
            if p in ("", "/"):
                continue
            if p == "/" + part:
                root_label = rest
                continue
            if not p.startswith("/" + part + "/"):
                continue
            if p.startswith("/" + part + "/lost\\+found") or p.startswith("/" + part + "/lost+found"):
                continue
            outc.append("/system" + p + " " + rest)
        outc.append(fpre + " " + (root_label or "u:object_r:system_file:s0"))
        with open(dst_fc, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write("\n".join(outc) + "\n")
    print("%s: %d entri digabung ke system (/%s)" % (part, added, part))


# ------------------------------------------------------------------ filter-props
def filter_props(base_files, files, drop_re):
    base = set()
    for b in base_files:
        base.update(read_props(b).keys())
    rx = re.compile(drop_re) if drop_re else None
    total = 0
    for f in files:
        if not os.path.isfile(f):
            continue
        lines = open(f, encoding="utf-8", errors="surrogateescape").read().splitlines()
        n = 0
        for i, line in enumerate(lines):
            s = line.strip()
            if not s or s.startswith("#") or s.startswith("import "):
                continue
            m = ANY_RE.match(s)
            if not m:
                continue
            k = m.group(1).strip()
            if k in base or (rx and rx.search(k)):
                lines[i] = "#[port] " + s
                n += 1
        if n:
            with open(f, "w", encoding="utf-8", errors="surrogateescape") as fh:
                fh.write("\n".join(lines) + "\n")
        total += n
        print("%s: %d props dinonaktifkan" % (f, n))
    print("TOTAL %d" % total)


# ------------------------------------------------------------------ odm-props
WANT_RE = re.compile(r"oplus|^ro\.separate\.soft$|^ro\.build\.device_family$|^ro\.commonsoft\.")


def odm_props(out, donor_files, base_files, skip_re):
    base = {}
    for b in base_files:
        base.update(read_props(b))
    rx = re.compile(skip_re, re.I) if skip_re else None
    got = {}
    for d in donor_files:
        for k, v in read_props(d).items():
            if not WANT_RE.search(k) or k in base:
                continue
            if k.startswith(("ro.boot.", "ctl.")) or (rx and rx.search(k)):
                continue
            got[k] = v
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("# [port-oplus] props OPlus non-hardware dari odm ROM donor (odm ingres tetap Xiaomi)\n")
        for k in sorted(got):
            f.write("%s=%s\n" % (k, got[k]))
    print("%d props odm OPlus -> %s" % (len(got), out))


# ------------------------------------------------------------------ merge-ids
def merge_ids(base, donor):
    def parse(p):
        rows = []
        for line in open(p, encoding="utf-8", errors="surrogateescape").read().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) >= 3:
                rows.append((parts[0], parts[2], line))
        return rows
    b = parse(base)
    names = {r[0] for r in b}
    ids = {r[1] for r in b}
    add = []
    skip = 0
    for name, ident, line in parse(donor):
        if name in names:
            continue
        if ident in ids:
            skip += 1
            continue
        add.append(line)
        names.add(name)
        ids.add(ident)
    if add:
        with open(base, "a", encoding="utf-8") as f:
            if not open(base, encoding="utf-8").read().endswith("\n"):
                f.write("\n")
            f.write("\n".join(add) + "\n")
    print("%s: +%d entri donor (%d dilewati karena id bentrok)" % (os.path.basename(base), len(add), skip))


# ------------------------------------------------------------------ identity
def identity(root):
    props = {}
    for f in sorted(glob.glob(os.path.join(root, "system", "my_*", "build.prop"))) + \
            [os.path.join(root, "product", "etc", "build.prop"), os.path.join(root, "system", "system", "build.prop")]:
        for k, v in read_props(f).items():
            props.setdefault(k, v)
    brand = (props.get("ro.product.product.brand") or props.get("ro.product.brand") or "").lower()
    region = (props.get("ro.oplus.image.my_region.type") or props.get("ro.vendor.oplus.regionmark") or "").upper()
    disp = props.get("ro.build.display.id.show") or props.get("ro.build.display.id") or ""
    if "realme" in brand:
        name = "realmeUI"
    elif "oneplus" in brand and not region.startswith("CN"):
        name = "OxygenOS"
    else:
        name = "ColorOS"
    m = re.search(r"(\d+\.\d+\.\d+\.\d+(?:\([^)]*\))?)", disp)
    ver = m.group(1) if m else (props.get("ro.build.version.oplusrom.display") or
                                props.get("ro.build.version.oplusrom") or "A" + props.get("ro.build.version.release", "?"))
    ver = re.sub(r"[^A-Za-z0-9._-]+", "_", ver).strip("_")
    print("%s %s" % (name, ver or "unknown"))


# ------------------------------------------------------------------ label-check
def label_check(root):
    fc = os.path.join(root, "config", "system_file_contexts")
    used = {}
    for line in open(fc, encoding="utf-8", errors="surrogateescape"):
        parts = line.split()
        if len(parts) < 2 or not parts[0].startswith("/system/my_"):
            continue
        ctx = parts[-1].split(":")
        if len(ctx) >= 3:
            used.setdefault(ctx[2], parts[0])
    known = set()
    cils = [os.path.join(root, "system", "system", "etc", "selinux", "plat_sepolicy.cil")]
    cils += glob.glob(os.path.join(root, "system_ext", "etc", "selinux", "*_sepolicy.cil"))
    cils += glob.glob(os.path.join(root, "product", "etc", "selinux", "*_sepolicy.cil"))
    rx = re.compile(r"^\((?:type|typealias)\s+([^\s()]+)\)", re.M)
    found = False
    for c in cils:
        if os.path.isfile(c):
            found = True
            known.update(rx.findall(open(c, encoding="utf-8", errors="surrogateescape").read()))
    if not found:
        print("RESULT ? (sepolicy donor tidak ditemukan)")
        return
    missing = sorted(t for t in used if t not in known)
    for t in missing:
        print("MISSING %s (mis. %s)" % (t, used[t].replace("\\", "")))
    print("RESULT %d %d" % (len(used), len(missing)))


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    cmd = argv[1]
    if cmd == "merge-config" and len(argv) == 4:
        merge_config(argv[2], argv[3])
    elif cmd in ("filter-props", "odm-props"):
        opts = {"--base": [], "--files": [], "--donor": [], "--drop-re": [], "--skip-re": []}
        pos = []
        cur = None
        for a in argv[2:]:
            if a in opts:
                cur = a
            elif cur:
                opts[cur].append(a)
            else:
                pos.append(a)
        if cmd == "filter-props":
            filter_props(opts["--base"], opts["--files"], (opts["--drop-re"] or [""])[0])
        else:
            if len(pos) != 1:
                raise SystemExit(__doc__)
            odm_props(pos[0], opts["--donor"], opts["--base"], (opts["--skip-re"] or [""])[0])
    elif cmd == "merge-ids" and len(argv) == 4:
        merge_ids(argv[2], argv[3])
    elif cmd == "identity" and len(argv) == 3:
        identity(argv[2])
    elif cmd == "label-check" and len(argv) == 3:
        label_check(argv[2])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
