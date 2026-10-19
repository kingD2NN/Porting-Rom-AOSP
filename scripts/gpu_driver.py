#!/usr/bin/env python3
"""
gpu_driver.py - helper addon gpudriver (driver GPU Adreno userspace dari vendor ROM donor / zip)

  gpu_driver.py root DIR
        cari root vendor di DIR (folder yang berisi lib64/egl/libGLESv2_adreno.so). Untuk zip
        modul Magisk/KSU (system/vendor/...), zip recovery (vendor/...) atau folder vendor hasil ekstrak.
  gpu_driver.py info VENDOR_ROOT
        version=0863.1        versi driver (string V@xxxx.y di libGLESv2_adreno.so)
        gpus=730 740 ...      GPU yang dikenal libgsl.so (tabel "Adreno (TM) NNN")
        bits=64 32            lib64 / lib yang punya driver lengkap
  gpu_driver.py newer A B
        exit 0 kalau versi A > B (format 0863.1)
  gpu_driver.py plan --src SRC --base BASE [--base DIR ...] [--bits 64,32] [--banned a.so,b.so]
        daftar path (relatif ke SRC) yang dipasang ke vendor base: file driver Adreno + library
        DT_NEEDED yang tidak ada di vendor base tapi ada di SRC (rekursif). Firmware (a730_sqe.fw,
        gmu_*.bin) TIDAK diambil: dimuat kernel (kgsl), harus pasangan kernel ingres.
        exit 2 = driver butuh library terlarang (pemicu bootloop yang sudah terbukti)
  gpu_driver.py labels --cfg CONFIG_FC --fc RUNTIME_FC [--donor-cfg F] --types F [--part vendor] REL...
        label SELinux untuk path baru:
          CTX <rel>=<label>        -> addon_tool.py ctx (config repack)
          FC <baris file_contexts> -> ditambahkan ke vendor/etc/selinux/vendor_file_contexts
        Prioritas: regex vendor_file_contexts base yang sudah cocok (selain vendor_file) ->
        label asli dari ROM donor (kalau type-nya dikenal policy) -> same_process_hal_file
        (library driver dimuat di proses app & surfaceflinger, wajib same_process_hal_file).
"""
import argparse
import fnmatch
import os
import re
import struct
import sys

LIBDIRS = {64: "lib64", 32: "lib"}
# file driver Adreno (glob, relatif ke lib/ atau lib64/). egl/* & hw/vulkan.adreno.so selalu.
DRIVER_GLOBS = [
    "egl/*", "hw/vulkan.adreno.so",
    "libgsl.so", "libCB.so", "libkcl.so", "libkernelmanager.so", "libgpudataproducer.so",
    "libEGL_adreno.so", "libGLESv1_CM_adreno.so", "libGLESv2_adreno.so",
    "libq3dtools_*.so", "libadreno*.so", "libllvm-*.so", "libOpenCL*.so", "libVkLayer_*ADRENO*.so",
    "libVkLayer_*adreno*.so", "libgpukbc*.so", "libgpu_tonemapper.so",
]
EXTRA_DIRS = ["gpu"]          # vendor/gpu/kbc (kernel binary cache, versinya ikut driver)
# pemicu bootloop yang sudah terbukti di AxionOS QPR2 ingres -> tidak pernah dipasang
BANNED_DEFAULT = "gpu++.so,libgpumemtracer.so,libgpuservice.so,libmapperutils.so,vendor.qti.hardware.hexlp-V2-ndk.so"
DT_NEEDED, DT_STRTAB = 1, 5


def elf_needed(path):
    """[needed] atau None (bukan ELF). Parser kecil, little-endian 32/64."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if data[:4] != b"\x7fELF" or data[5] != 1:
        return None
    is64 = data[4] == 2
    try:
        if is64:
            phoff, = struct.unpack_from("<Q", data, 0x20)
            phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
        else:
            phoff, = struct.unpack_from("<I", data, 0x1C)
            phentsize, phnum = struct.unpack_from("<HH", data, 0x2A)
        loads, dyn = [], None
        for i in range(phnum):
            o = phoff + i * phentsize
            if is64:
                ptype, _fl, off, vaddr, _pa, filesz = struct.unpack_from("<IIQQQQ", data, o)
            else:
                ptype, off, vaddr, _pa, filesz = struct.unpack_from("<IIIII", data, o)
            if ptype == 1:
                loads.append((vaddr, off, filesz))
            elif ptype == 2:
                dyn = (off, filesz)
        if not dyn:
            return []

        def v2o(v):
            for va, off, sz in loads:
                if va <= v < va + sz:
                    return v - va + off
            return None
        ents, strtab = [], None
        step = 16 if is64 else 8
        for o in range(dyn[0], dyn[0] + dyn[1], step):
            tag, val = struct.unpack_from("<qQ" if is64 else "<iI", data, o)
            if tag == 0:
                break
            if tag == DT_NEEDED:
                ents.append(val)
            elif tag == DT_STRTAB:
                strtab = v2o(val)
        if strtab is None:
            return []
        out = []
        for v in ents:
            e = data.index(b"\0", strtab + v)
            out.append(data[strtab + v:e].decode("utf-8", "replace"))
        return out
    except (struct.error, ValueError, IndexError):
        return []


def find_root(d):
    for root, dirs, files in os.walk(d):
        dirs.sort()
        if os.path.basename(root) == "egl" and "libGLESv2_adreno.so" in files:
            lib = os.path.dirname(root)
            if os.path.basename(lib) == "lib64":
                return os.path.dirname(lib)
    return None


def read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


def version_of(root):
    for bits in (64, 32):
        b = read(os.path.join(root, LIBDIRS[bits], "egl", "libGLESv2_adreno.so"))
        m = re.search(rb"V@(\d+)\.(\d+)", b)
        if m:
            return "%s.%s" % (m.group(1).decode(), m.group(2).decode())
    return ""


def vtuple(v):
    try:
        a, _, b = v.partition(".")
        return (int(a), int(b or 0))
    except ValueError:
        return (0, 0)


def cmd_info(a):
    root = a.root
    gpus = set()
    for bits in (64, 32):
        b = read(os.path.join(root, LIBDIRS[bits], "libgsl.so"))
        gpus |= {m.decode() for m in re.findall(rb"Adreno \(TM\) (\d{3,4})", b)}
    bits = [str(x) for x in (64, 32) if os.path.isfile(os.path.join(root, LIBDIRS[x], "egl", "libGLESv2_adreno.so"))
            and os.path.isfile(os.path.join(root, LIBDIRS[x], "libgsl.so"))]
    print("version=%s" % version_of(root))
    print("gpus=%s" % " ".join(sorted(gpus, key=int)))
    print("bits=%s" % " ".join(bits))
    return 0


def cmd_newer(a):
    return 0 if vtuple(a.a) > vtuple(a.b) else 1


def driver_files(src, libdir):
    base = os.path.join(src, libdir)
    out = []
    if not os.path.isdir(base):
        return out
    for root, dirs, files in os.walk(base):
        dirs.sort()
        rel_root = os.path.relpath(root, base)
        for f in sorted(files + [d for d in dirs if os.path.islink(os.path.join(root, d))]):
            rel = f if rel_root == "." else os.path.join(rel_root, f)
            if any(fnmatch.fnmatch(rel, g) for g in DRIVER_GLOBS):
                out.append(os.path.join(libdir, rel))
    return out


def cmd_plan(a):
    src = a.src
    bases = a.base
    banned = {x for x in (a.banned or BANNED_DEFAULT).split(",") if x}
    bits_list = [int(x) for x in a.bits.split(",") if x]
    plan, bad = [], []
    for bits in bits_list:
        libdir = LIBDIRS[bits]
        files = driver_files(src, libdir)
        if not any(f.endswith("egl/libGLESv2_adreno.so") for f in files):
            continue
        staged = {os.path.basename(f) for f in files}
        queue = [f for f in files if not os.path.islink(os.path.join(src, f))]
        seen = set()
        while queue:
            rel = queue.pop(0)
            if rel in seen:
                continue
            seen.add(rel)
            for n in elf_needed(os.path.join(src, rel)) or []:
                if n in staged:
                    continue
                if any(os.path.exists(os.path.join(b, libdir, sub, n)) for b in bases for sub in ("", "egl", "hw")):
                    continue
                if n in banned:
                    # vendor base tidak punya & tidak boleh diambil dari sumber driver
                    bad.append("%s butuh %s" % (rel, n))
                    continue
                cand = os.path.join(libdir, n)
                if os.path.isfile(os.path.join(src, cand)):
                    print("  dependensi baru dari sumber driver: %s (dibutuhkan %s)" % (cand, os.path.basename(rel)),
                          file=sys.stderr)
                    files.append(cand)
                    staged.add(n)
                    queue.append(cand)
                # selain itu: library system/LLNDK -> dicek addon_deps (linker_check)
        for f in files:
            if os.path.basename(f) in banned:
                print("  dilewati (terlarang, tidak dipasang): %s" % f, file=sys.stderr)
        plan += [f for f in files if os.path.basename(f) not in banned]
    for d in EXTRA_DIRS:
        top = os.path.join(src, d)
        for root, dirs, fs in os.walk(top):
            dirs.sort()
            for f in sorted(fs):
                plan.append(os.path.relpath(os.path.join(root, f), src))
    for b in bad:
        print("TERLARANG: " + b, file=sys.stderr)
    for p in dict.fromkeys(plan):
        print(p)
    return 2 if bad else 0


def load_fc(path):
    """[(regex|None, exact_path|None, label)]"""
    out = []
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return out
    for line in lines:
        s = line.split("#", 1)[0].split()
        if len(s) < 2:
            continue
        spec, label = s[0], s[-1]
        if not re.search(r"[\\.^$|?*+()\[\]{}]", spec.replace("\\.", "")):
            out.append((None, spec.replace("\\", ""), label))
            continue
        try:
            out.append((re.compile(spec + r"\Z"), None, label))
        except re.error:
            continue
    return out


def fc_lookup(entries, path):
    exact = [lab for rx, ex, lab in entries if ex == path]
    if exact:
        return exact[-1]
    hit = None
    for rx, ex, lab in entries:
        if rx is not None and rx.match(path):
            hit = lab
    return hit


def load_cfg(path):
    m = {}
    try:
        for line in open(path, encoding="utf-8", errors="replace"):
            p = line.split()
            if len(p) >= 2:
                m[p[0].replace("\\", "")] = p[1]
    except OSError:
        pass
    return m


def ctx_type(label):
    p = label.split(":")
    return p[2] if len(p) >= 3 else ""


def esc(path):
    return re.sub(r"([^-_/a-zA-Z0-9])", r"\\\1", path)


def cmd_labels(a):
    part = a.part
    cfg = load_cfg(a.cfg)
    donor = load_cfg(a.donor_cfg) if a.donor_cfg else {}
    fc = load_fc(a.fc)
    types = set(open(a.types, encoding="utf-8", errors="replace").read().split()) if a.types else set()
    sp = "u:object_r:same_process_hal_file:s0"
    for rel in a.rel:
        rel = rel.strip("/")
        key = "/%s/%s" % (part, rel)
        if key in cfg:
            continue
        if rel.split("/", 1)[0] not in ("lib", "lib64", "gpu"):
            print("CTX %s" % rel)      # bukan library driver: label saudara (addon_tool.py ctx)
            continue
        rt = fc_lookup(fc, key)
        if rt and ctx_type(rt) not in ("vendor_file", ""):
            label = rt
        else:
            dl = donor.get(key) or donor.get("/" + rel)
            label = dl if dl and ctx_type(dl) in types and ctx_type(dl) != "vendor_file" else sp
        print("CTX %s=%s" % (rel, label))
        if rt != label:
            print("FC /(vendor|system/vendor)/%s %s" % (esc(rel), label))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("root"); p.add_argument("dir")
    p = sp.add_parser("info"); p.add_argument("root")
    p = sp.add_parser("newer"); p.add_argument("a"); p.add_argument("b")
    p = sp.add_parser("plan")
    p.add_argument("--src", required=True)
    p.add_argument("--base", action="append", default=[])
    p.add_argument("--bits", default="64,32")
    p.add_argument("--banned", default="")
    p = sp.add_parser("labels")
    p.add_argument("--cfg", required=True)
    p.add_argument("--fc", required=True)
    p.add_argument("--donor-cfg", default="")
    p.add_argument("--types", default="")
    p.add_argument("--part", default="vendor")
    p.add_argument("rel", nargs="*")
    a = ap.parse_args()
    if a.cmd == "root":
        r = find_root(a.dir)
        if not r:
            return 1
        print(r)
        return 0
    return {"info": cmd_info, "newer": cmd_newer, "plan": cmd_plan, "labels": cmd_labels}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
