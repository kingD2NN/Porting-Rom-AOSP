#!/usr/bin/env python3
"""
dl_helper.py - bantu download ROM dari link "halaman web" (Google Drive, SourceForge,
Pixeldrain, MediaFire, Gofile) yang kalau diunduh mentah menghasilkan HTML, bukan file.

  dl_helper.py normalize <url>        -> URL unduh langsung (atau URL asal kalau tidak dikenal)
  dl_helper.py host <url>             -> gdrive | sourceforge | pixeldrain | mediafire | gofile | other
  dl_helper.py gofile <url>           -> baris "link=", "cookie=", "ua=", "name=" untuk download Gofile
                                         (akun tamu / env GOFILE_TOKEN); exit 1 + pesan di stderr kalau gagal
  dl_helper.py gdrive-form <html>     -> URL lanjutan dari halaman konfirmasi Google Drive
                                         ("can't scan for viruses"); exit 1 kalau tidak ada form
  dl_helper.py mediafire <html>       -> URL file asli dari halaman MediaFire; exit 1 kalau tidak ada
  dl_helper.py sniff <file>           -> html | zip | payload | gzip | xz | zstd | android | sparse | unknown
  dl_helper.py html-reason <html>     -> alasan singkat kalau HTML berisi pesan error yang dikenali
  dl_helper.py signed <url>           -> link bertanda tangan sementara (S3/Spaces/R2, X-Amz-Date +
                                         X-Amz-Expires): "<detik_sisa> <kedaluwarsa_UTC> <masa_berlaku>",
                                         kosong kalau bukan link seperti itu
"""
import html as htmlmod
import re
import sys
from urllib.parse import parse_qs, urlencode, urlparse


def gdrive_id(url):
    u = urlparse(url)
    if not u.netloc.endswith(("drive.google.com", "docs.google.com", "drive.usercontent.google.com")):
        return None
    m = re.search(r"/(?:file/)?d/([A-Za-z0-9_-]{10,})", u.path)
    if m:
        return m.group(1)
    q = parse_qs(u.query)
    if "id" in q:
        return q["id"][0]
    return None


def host(url):
    n = urlparse(url).netloc.lower()
    if gdrive_id(url):
        return "gdrive"
    if "sourceforge.net" in n:
        return "sourceforge"
    if "pixeldrain" in n:
        return "pixeldrain"
    if "mediafire.com" in n:
        return "mediafire"
    if n == "gofile.io" or n.endswith(".gofile.io"):
        return "gofile"
    return "other"


def normalize(url):
    gid = gdrive_id(url)
    if gid:
        return "https://drive.usercontent.google.com/download?id=%s&export=download&confirm=t" % gid
    u = urlparse(url)
    n = u.netloc.lower()
    # SourceForge: link mirror bertoken (?ts=...) kedaluwarsa -> bentuk stabil .../files/<path>/download
    if n == "downloads.sourceforge.net" or n.endswith(".dl.sourceforge.net"):
        m = re.match(r"/project/([^/]+)/(.+)", u.path)
        if m:
            return "https://sourceforge.net/projects/%s/files/%s/download" % (m.group(1), m.group(2))
    if n.endswith("sourceforge.net"):
        m = re.match(r"/projects/([^/]+)/files/(.+?)(/download)?/?$", u.path)
        if m:
            return "https://sourceforge.net/projects/%s/files/%s/download" % (m.group(1), m.group(2))
    # Pixeldrain: /u/<id> (halaman) -> /api/file/<id>?download
    if "pixeldrain" in n:
        m = re.match(r"/(?:u|l|api/file)/([A-Za-z0-9]+)", u.path)
        if m:
            return "https://%s/api/file/%s?download" % (u.netloc, m.group(1))
    return url


def gdrive_form(page):
    m = re.search(r'<form[^>]*id="download-form"[^>]*action="([^"]+)"(.*?)</form>', page, re.S)
    if not m:
        m = re.search(r'<form[^>]*action="([^"]*download[^"]*)"(.*?)</form>', page, re.S)
    if not m:
        return None
    action = htmlmod.unescape(m.group(1))
    fields = re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', m.group(2))
    if not fields:
        return None
    return action + ("&" if "?" in action else "?") + urlencode([(k, htmlmod.unescape(v)) for k, v in fields])


def mediafire(page):
    m = re.search(r'href="(https?://download[0-9]*\.mediafire\.com/[^"]+)"', page)
    return htmlmod.unescape(m.group(1)) if m else None


def sniff(path):
    with open(path, "rb") as f:
        head = f.read(512)
    if head[:4] == b"PK\x03\x04":
        return "zip"
    if head[:4] == b"CrAU":
        return "payload"
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    if head[:6] == b"\xfd7zXZ\x00":
        return "xz"
    if head[:4] == b"\x28\xb5\x2f\xfd":
        return "zstd"
    if head[:8] == b"ANDROID!" or head[:8] == b"VNDRBOOT":
        return "android"
    if head[:4] == b"\x3a\xff\x26\xed":
        return "sparse"
    t = head.lstrip().lower()
    if t.startswith((b"<!doctype", b"<html", b"<head", b"<?xml", b"<body", b"<script", b"{")):
        return "html"
    return "unknown"


def html_reason(page):
    p = page.lower()
    if "too many users have viewed or downloaded" in p or "quota" in p and "exceeded" in p:
        return "kuota download Google Drive file ini habis (coba lagi 24 jam, atau upload ulang / pakai host lain)"
    if "accounts.google.com" in p and ("sign in" in p or "servicelogin" in p):
        return "file Google Drive butuh login: ubah share ke 'Anyone with the link'"
    if "you need access" in p or "request access" in p:
        return "file Google Drive private: ubah share ke 'Anyone with the link'"
    if "file not found" in p or "not found" in p and "404" in p:
        return "file tidak ditemukan / sudah dihapus"
    return "server mengirim halaman HTML, bukan file ROM (link halaman web, bukan link unduh langsung)"


# ------------------------------------------------------------------ gofile
# Link Gofile (halaman gofile.io/d/<kode> maupun link file <server>.gofile.io/download/web/<id>/<nama>)
# hanya memberi file kalau request membawa cookie accountToken=<token akun>. Tanpa cookie -> halaman
# HTML. Token akun tamu dibuat lewat POST https://api.gofile.io/accounts (sama seperti situsnya).
# Isi folder dibaca lewat /contents/<kode> dengan header X-Website-Token = sha256(UA::bahasa::token::
# jendela_4_jam::salt) (algoritma gofile.io/dist/js/wt.obf.js, sama dengan gallery-dl). Salt bisa
# diganti gofile sewaktu-waktu -> env GOFILE_SALT. Env GOFILE_TOKEN = token akun sendiri (premium).
GOFILE_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/138.0.0.0 Safari/537.36")
GOFILE_SALT = "12af056dacea0b"


def _gofile_api(path, token=None, method="GET", params=None, wt=None):
    import json
    import urllib.request
    import os
    url = (os.environ.get("GOFILE_API") or "https://api.gofile.io") + path   # GOFILE_API: untuk tes
    if params:
        url += "?" + urlencode(params)
    h = {"User-Agent": GOFILE_UA, "Accept": "application/json", "Origin": "https://gofile.io",
         "Referer": "https://gofile.io/"}
    if token:
        h["Authorization"] = "Bearer " + token
        h["Cookie"] = "accountToken=" + token
    if wt:
        h["X-Website-Token"] = wt
        h["X-BL"] = "en-US"
    import time
    res = {"status": "?"}
    for attempt in range(4):
        req = urllib.request.Request(url, data=b"" if method == "POST" else None, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            try:
                res = json.loads(e.read().decode("utf-8", "replace"))
            except ValueError:
                res = {"status": "http-%d" % e.code}
            if e.code != 429 and e.code < 500:
                return res
        except (urllib.error.URLError, OSError, ValueError) as e:
            res = {"status": "jaringan: %s" % getattr(e, "reason", e)}
        time.sleep(20 * (attempt + 1))   # 429 / 5xx / jaringan: tunggu lalu ulang
    return res


def _gofile_wt(token, now=None):
    import hashlib
    import os
    import time
    now = time.time() if now is None else now
    salt = os.environ.get("GOFILE_SALT") or GOFILE_SALT
    data = "%s::en-US::%s::%d::%s" % (GOFILE_UA, token, int(now / 14400), salt)
    return hashlib.sha256(data.encode()).hexdigest()


def gofile_parse(url):
    """('folder', kode) | ('file', nama) | None"""
    from urllib.parse import unquote
    u = urlparse(url)
    m = re.match(r"^/d/([A-Za-z0-9_-]+)", u.path)
    if m:
        return "folder", m.group(1)
    m = re.match(r"^/download/(?:web|direct)/[0-9a-fA-F-]{8,}/(.+)$", u.path)
    if m:
        return "file", unquote(m.group(1))
    return None


def gofile_pick(children, want=None):
    files = [c for c in children if c.get("type") == "file" and c.get("link")]
    if want:
        files = [c for c in files if c.get("name") == want] or files
    if not files:
        return None
    rom = [c for c in files if re.search(r"\.(zip|tgz|tar\.(gz|xz|zst)|7z|bin|img)$", c.get("name", ""), re.I)]
    return max(rom or files, key=lambda c: int(c.get("size") or 0))


def gofile(url):
    import os
    kind = gofile_parse(url)
    if not kind:
        sys.stderr.write("link Gofile tidak dikenali (pakai gofile.io/d/<kode> atau link file "
                         "<server>.gofile.io/download/web/<id>/<nama>)\n")
        return 1
    token = os.environ.get("GOFILE_TOKEN", "").strip()
    if not token:
        r = _gofile_api("/accounts", method="POST")
        token = (r.get("data") or {}).get("token") if r.get("status") == "ok" else None
        if not token:
            sys.stderr.write("gagal membuat akun tamu Gofile (status: %s). Coba lagi nanti, atau isi "
                             "secret GOFILE_TOKEN dengan token akun Gofile sendiri\n" % r.get("status"))
            return 1
    link, name = url, None
    if kind[0] == "file":
        name = kind[1]
    else:
        r = _gofile_api("/contents/" + kind[1], token=token, wt=_gofile_wt(token),
                        params={"contentFilter": "", "page": "1", "pageSize": "1000",
                                "sortField": "name", "sortDirection": "1"})
        st = r.get("status")
        if st != "ok":
            why = {"error-notFound": "folder tidak ditemukan / sudah dihapus (file Gofile akun gratis "
                                     "terhapus otomatis kalau lama tidak diunduh)",
                   "error-passwordRequired": "folder Gofile dikunci password",
                   "error-notPremium": "file ini hanya bisa diunduh akun premium Gofile (isi secret GOFILE_TOKEN)",
                   "error-wrongToken": "website token Gofile ditolak (salt berubah? isi env GOFILE_SALT)",
                   "error-rateLimit": "kena batas request Gofile, coba lagi beberapa menit lagi"}.get(st, st)
            sys.stderr.write("Gofile /contents/%s: %s\n" % (kind[1], why))
            return 1
        d = r.get("data") or {}
        ch = d.get("children") or {}
        ch = list(ch.values()) if isinstance(ch, dict) else list(ch)
        if d.get("type") == "file":
            ch = [d]
        f = gofile_pick(ch)
        if not f:
            sys.stderr.write("folder Gofile %s tidak berisi file\n" % kind[1])
            return 1
        link, name = f["link"], f.get("name")
    print("link=" + link)
    print("cookie=accountToken=" + token)
    print("ua=" + GOFILE_UA)
    print("name=" + (name or ""))
    return 0


def signed(url, now=None):
    """(sisa_detik, waktu_kedaluwarsa_UTC, masa_berlaku_detik) untuk presigned URL AWS SigV4, atau None"""
    import calendar
    import time
    q = {k.lower(): v[0] for k, v in parse_qs(urlparse(url).query).items()}
    d, e = q.get("x-amz-date"), q.get("x-amz-expires")
    if not d or not e or not e.isdigit():
        return None
    try:
        t0 = calendar.timegm(time.strptime(d, "%Y%m%dT%H%M%SZ"))
    except ValueError:
        return None
    end = t0 + int(e)
    now = time.time() if now is None else now
    return int(end - now), time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(end)), int(e)


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    cmd, arg = sys.argv[1], sys.argv[2]
    if cmd == "gofile":
        return gofile(arg)
    if cmd == "normalize":
        print(normalize(arg))
    elif cmd == "host":
        print(host(arg))
    elif cmd in ("gdrive-form", "mediafire", "html-reason"):
        page = open(arg, encoding="utf-8", errors="replace").read()
        if cmd == "html-reason":
            print(html_reason(page))
            return 0
        out = gdrive_form(page) if cmd == "gdrive-form" else mediafire(page)
        if not out:
            return 1
        print(out)
    elif cmd == "sniff":
        print(sniff(arg))
    elif cmd == "signed":
        r = signed(arg)
        if r:
            print("%d %s %d" % (r[0], r[1].replace(" ", "_"), r[2]))
    else:
        raise SystemExit(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
