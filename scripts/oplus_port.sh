#!/usr/bin/env bash
# =============================================================================
#  oplus_port.sh - donor OxygenOS / ColorOS / realme UI (OPlus) -> ingres (di-source port.sh)
#
#  ROM OPlus membawa partisi my_* (my_product, my_stock, my_heytap, my_manifest, ...). Di HP
#  OnePlus partisi itu di-mount first-stage ke /mnt/vendor/my_* lalu di-bind ke /my_* oleh fstab
#  vendor OPlus. Vendor Xiaomi ingres tidak punya baris itu, jadi (seperti port ColorOS/OxygenOS
#  ke HP Xiaomi lain) vendor/odm/vendor_dlkm/firmware tetap Xiaomi, framework dari OPlus:
#    - system, system_ext, product = donor OPlus (partisi port biasa)
#    - my_* DIGABUNG ke root image system sebagai folder /my_* (label SELinux asli dipertahankan)
#    - /mnt/vendor/my_* dibuat lagi saat early-init (bind dari /my_*) untuk kode OPlus yang
#      memakai path itu
#    - props my_*/build.prop di-import dari /odm/etc/build.prop (init AOSP tidak memuatnya
#      sendiri); key yang diisi vendor/odm base dibuang -> props hardware Xiaomi tetap menang
#    - props OPlus non-hardware & flag fitur OPlus (odm/etc/permissions) dari odm donor
#    - passwd/group vendor OPlus digabung tanpa menimpa entri Xiaomi
#    - Tentang ponsel: nama POCO F4 GT, CPU, kamera, layar ingres; brand/model donor dipertahankan
#      (banyak fitur OPlus dikunci ke brand/model)
#    - debloat debloat_oplus.txt: kamera OPlus (butuh HAL kamera OnePlus), overlay sidik jari di
#      layar (ingres: sidik jari samping), stub pihak ketiga, aplikasi Google opsional
#
#  Memakai helper & variabel port.sh: log ok warn die is_true get_prop set_prop extract_img
#    P_IMG P_FS B_FS WORK SCRIPT_DIR BIN EXTRACT_EROFS LCD_DENSITY ROM_MAINTAINER DEVICE_SPEC_*
# =============================================================================

PORT_FLAVOR=${PORT_FLAVOR:-auto}             # auto | aosp | oplus
OPLUS_MY_PARTS=${OPLUS_MY_PARTS:-"my_bigball my_carrier my_company my_engineering my_heytap my_manifest my_preload my_product my_region my_stock"}
# urutan import props (yang belakangan menang); my_manifest = identitas build, paling akhir
OPLUS_PROP_ORDER=${OPLUS_PROP_ORDER:-"my_heytap my_stock my_preload my_bigball my_carrier my_region my_company my_engineering my_product my_manifest"}
OPLUS_DEBLOAT=${OPLUS_DEBLOAT:-true}
OPLUS_DEBLOAT_FILE=${OPLUS_DEBLOAT_FILE:-$SCRIPT_DIR/../debloat_oplus.txt}
OPLUS_MARKET_NAME=${OPLUS_MARKET_NAME:-POCO F4 GT}
OPLUS_SCREEN_INCH=${OPLUS_SCREEN_INCH:-6.67}
# props my_* khusus panel/resolusi donor (ingres: 1080p, density dari base)
OPLUS_DROP_PROP_RE=${OPLUS_DROP_PROP_RE:-'^ro\.sf\.lcd_density$|^ro\.density\.screenzoom\.|^ro\.oplus\.density\.|^ro\.oplus\.resolution\.|screenhole|^ro\.product\.first_api_level$|^ro\.zygote$'}
# props odm donor yang menyangkut hardware donor tidak diambil
OPLUS_ODM_SKIP_RE=${OPLUS_ODM_SKIP_RE:-'display|camera|screen|lcd|panel|finger|charg|battery|audio|sensor|vibrat|haptic|nfc|aod|hbm|refresh|density|resolution|touch|wlan|wifi|bluetooth|modem|radio|gpu|cpu|thermal|power|sku|hardware'}
OPLUS_PRESENT=""                              # my_* yang ada di donor (diisi oplus_detect)
OPLUS_DIR=""                                  # odm & vendor donor (ekstrak sebagian)

# want payload donor: kalau payload punya my_manifest/my_product, ikut ambil my_* + odm/vendor donor
oplus_payload_want() { # pfx avail want -> want
    local pfx=$1 avail=$2 want=$3 p
    if [[ $pfx != port || $PORT_FLAVOR == aosp || $want == all ]]; then echo "$want"; return 0; fi
    if in_list my_manifest "$avail" || in_list my_product "$avail"; then
        for p in $OPLUS_MY_PARTS odm vendor; do want+=",$p"; done
    fi
    echo "$want"
}

oplus_detect() {
    local p
    OPLUS_PRESENT=""
    for p in $OPLUS_MY_PARTS; do
        if [[ -f $P_IMG/$p.img ]]; then OPLUS_PRESENT+="$p "; fi
    done
    OPLUS_PRESENT=${OPLUS_PRESENT% }
    if [[ -n $OPLUS_PRESENT ]]; then
        if [[ $PORT_FLAVOR == aosp ]]; then
            warn "donor punya partisi OPlus ($OPLUS_PRESENT) tapi PORT_FLAVOR=aosp -> my_* diabaikan (ROM OPlus tidak akan jalan tanpa my_*)"
            return 0
        fi
        PORT_FLAVOR=oplus
        ok "donor OxygenOS/ColorOS (OPlus): partisi $OPLUS_PRESENT -> digabung ke /system"
    elif [[ $PORT_FLAVOR == oplus ]]; then
        die "PORT_FLAVOR=oplus tapi ROM donor tidak punya partisi my_* (bukan OTA OxygenOS/ColorOS). Pakai OTA full OnePlus/OPPO/realme (.zip payload.bin)"
    else
        PORT_FLAVOR=aosp
    fi
}

# ekstrak sebagian isi image: erofs (extract.erofs -X) / ext4 (debugfs rdump) -> out/<nama>/<path>
oplus_extract_paths() { # img out path...
    local img=$1 out=$2 name t p
    shift 2
    name=$(basename "$img" .img)
    if python3 "$SCRIPT_DIR/lp_tool.py" sparse "$img"; then simg2img "$img" "$img.raw"; mv -f "$img.raw" "$img"; fi
    t=$(gettype -i "$img" 2>/dev/null || true)
    mkdir -p "$out/$name"
    for p in "$@"; do
        case $t in
            erofs)
                "${EXTRACT_EROFS:-$BIN/extract.erofs}" -i "$img" -o "$out" -X "$p" -f >/dev/null 2>&1 \
                    || "$BIN/extract.erofs" -i "$img" -o "$out" -X "$p" -f >/dev/null 2>&1 || true ;;
            ext)
                mkdir -p "$out/$name/$(dirname "$p")"
                debugfs -R "rdump /$p $out/$name/$(dirname "$p")" "$img" >/dev/null 2>&1 || true ;;
            *) warn "OPlus: $name.img tipe '$t' tidak dikenal, $p tidak diambil" ;;
        esac
    done
}

oplus_extract() {
    local p
    [[ $PORT_FLAVOR == oplus ]] || return 0
    for p in $OPLUS_PRESENT; do
        log "ekstrak port $p (OPlus)"; extract_img "$P_IMG/$p.img" "$P_FS"; rm -f "$P_IMG/$p.img"
    done
    OPLUS_DIR="$WORK/port/oplus"
    rm -rf "$OPLUS_DIR"; mkdir -p "$OPLUS_DIR"
    if [[ -f $P_IMG/odm.img ]]; then
        oplus_extract_paths "$P_IMG/odm.img" "$OPLUS_DIR" build.prop etc/build.prop etc/permissions
        rm -f "$P_IMG/odm.img"
    else
        warn "OPlus: odm donor tidak ada di payload, props & flag fitur OPlus dari odm tidak diambil"
    fi
    if [[ -f $P_IMG/vendor.img ]]; then
        oplus_extract_paths "$P_IMG/vendor.img" "$OPLUS_DIR" etc/passwd etc/group
        rm -f "$P_IMG/vendor.img"
    fi
    rm -rf "$OPLUS_DIR/config"
}

# my_* -> folder di root image system (/my_*), config fs & label digabung ke config system
oplus_merge_my() {
    local p root="$P_FS/system" res
    [[ $PORT_FLAVOR == oplus ]] || return 0
    [[ -d $root/system ]] || die "OPlus: system donor belum diekstrak, my_* tidak bisa digabung"
    for p in $OPLUS_PRESENT; do
        [[ -d $P_FS/$p ]] || { warn "OPlus: $p tidak terekstrak, dilewati"; continue; }
        rm -rf "${root:?}/$p"            # titik mount kosong / symlink ke /mnt/vendor/my_*
        mv "$P_FS/$p" "$root/$p"
        rm -rf "${root:?}/$p/lost+found"
        res=$(python3 "$SCRIPT_DIR/oplus_tool.py" merge-config "$P_FS" "$p") || die "OPlus: gabung config $p gagal"
        rm -f "$P_FS/config/${p}_"*
        log "  $res"
    done
    ok "OPlus: $(wc -w <<< "$OPLUS_PRESENT") partisi my_* jadi bagian image system (ukuran system $(du -sh "$root" | cut -f1))"
    # label file my_* harus dikenal sepolicy system/system_ext/product donor
    res=$(python3 "$SCRIPT_DIR/oplus_tool.py" label-check "$P_FS" 2>/dev/null || echo "RESULT ?")
    case $(sed -n 's/^RESULT //p' <<< "$res") in
        *" 0") ok "OPlus: semua type label SELinux /my_* dikenal sepolicy donor" ;;
        "?"*) warn "OPlus: sepolicy donor tidak terbaca, label /my_* tidak dicek" ;;
        *) warn "OPlus: type label /my_* tidak ada di sepolicy donor (file jadi unlabeled):"
           grep '^MISSING' <<< "$res" | head -n 20 | sed 's/^/    /' >&2 ;;
    esac
}

oplus_density() {
    local d=$LCD_DENSITY
    if [[ $d == auto ]]; then
        d=$(get_prop "$B_FS/product/etc/build.prop" ro.sf.lcd_density)
        [[ -n $d ]] || d=$(base_hw_prop ro.sf.lcd_density)
    fi
    echo "$d"
}

# props: import my_* dari odm, buang props hardware/panel donor, identitas & spesifikasi ingres
oplus_props() {
    local sysroot="$P_FS/system" odm_prop="$B_FS/odm/etc/build.prop" p f imports="" tail="" files=() base_files=()
    local pdir="$sysroot/my_product/etc" res
    [[ -d $sysroot/my_product ]] || pdir="$sysroot/system/etc"
    mkdir -p "$pdir"
    for f in "$B_FS/vendor/build.prop" "$B_FS/vendor/default.prop" "$B_FS/odm/build.prop" "$B_FS"/odm/etc/*build.prop; do
        [[ -f $f ]] && base_files+=("$f")
    done
    for p in $OPLUS_PRESENT; do
        [[ -f $sysroot/$p/build.prop ]] && files+=("$sysroot/$p/build.prop")
    done
    if [[ ${#files[@]} -gt 0 ]]; then
        res=$(python3 "$SCRIPT_DIR/oplus_tool.py" filter-props --base "${base_files[@]}" --files "${files[@]}" \
              --drop-re "$OPLUS_DROP_PROP_RE")
        ok "OPlus props: $(sed -n 's/^TOTAL //p' <<< "$res") prop my_* dinonaktifkan (props hardware vendor/odm Xiaomi & panel donor tetap milik base)"
    fi
    # props odm OPlus (non-hardware) dari odm donor
    local donor_odm=()
    for f in "$OPLUS_DIR/odm/build.prop" "$OPLUS_DIR/odm/etc/build.prop"; do
        [[ -f $f ]] && donor_odm+=("$f")
    done
    if [[ ${#donor_odm[@]} -gt 0 ]]; then
        res=$(python3 "$SCRIPT_DIR/oplus_tool.py" odm-props "$pdir/port_oplus_odm.prop" --donor "${donor_odm[@]}" \
              --base "${base_files[@]}" --skip-re "$OPLUS_ODM_SKIP_RE")
        log "  $res"
    fi
    # spesifikasi & identitas ingres (file ini di-import paling akhir -> menang)
    local dens bfal disp
    dens=$(oplus_density)
    bfal=$(get_prop "$P_FS/product/etc/build.prop" ro.product.first_api_level)
    disp=$(cat "${files[@]}" /dev/null 2>/dev/null | sed -n 's/^ro\.build\.display\.id\.show=//p' | tail -n1)
    {
        echo "# [port-oplus] identitas & spesifikasi ingres di Tentang ponsel"
        echo "ro.vendor.oplus.market.name=$OPLUS_MARKET_NAME"
        echo "ro.vendor.oplus.market.enname=$OPLUS_MARKET_NAME"
        [[ -n ${DEVICE_SPEC_CPU:-} ]] && echo "ro.product.oplus.cpuinfo=$DEVICE_SPEC_CPU"
        [[ -n ${DEVICE_SPEC_CAMERA_REAR:-} ]] && echo "ro.vendor.oplus.camera.backCamSize=${DEVICE_SPEC_CAMERA_REAR// /}"
        [[ -n ${DEVICE_SPEC_CAMERA_FRONT:-} ]] && echo "ro.vendor.oplus.camera.frontCamSize=${DEVICE_SPEC_CAMERA_FRONT// /}"
        [[ -n $OPLUS_SCREEN_INCH ]] && echo "ro.oplus.display.screenSizeInches.primary=$OPLUS_SCREEN_INCH"
        [[ -n $dens ]] && echo "ro.sf.lcd_density=$dens"
        [[ -n $bfal ]] && echo "ro.product.first_api_level=$bfal"
        if [[ -n ${ROM_MAINTAINER:-} && -n $disp ]]; then
            echo "ro.build.display.id.show=$disp ${ROM_RELEASE_TYPE:-UNOFFICIAL} by $ROM_MAINTAINER"
        fi
    } > "$pdir/port_ingres.prop"
    # nilai yang sama juga langsung di my_manifest/my_product (kalau init OPlus memuatnya lagi)
    for f in "${files[@]}"; do
        sed -i -E "s/^(ro\.vendor\.oplus\.market\.(en)?name)=.*/\1=${OPLUS_MARKET_NAME//\//\\/}/" "$f"
    done
    # props dimuat init SEBELUM early-init (bind /mnt/vendor/my_* belum ada): import ke
    # /mnt/vendor/my_* diarahkan ke /my_* (isinya sama, sudah ada di image system)
    local n_imp=0
    while IFS= read -r -d '' f; do
        grep -qE '^[[:space:]]*import[[:space:]]+/mnt/vendor/my_' "$f" || continue
        sed -i -E 's#^([[:space:]]*import[[:space:]]+)/mnt/vendor/(my_)#\1/\2#' "$f"; n_imp=$((n_imp + 1))
    done < <(find "$sysroot"/my_* "$sysroot/system/build.prop" "$P_FS/system_ext/etc" "$P_FS/product/etc" \
                -maxdepth 3 -type f \( -name '*.prop' -o -name 'build.prop' \) -print0 2>/dev/null)
    if [[ $n_imp -gt 0 ]]; then log "  $n_imp file props: import /mnt/vendor/my_* -> /my_*"; fi
    # import dari /odm/etc/build.prop: my_* + props odm OPlus DI AWAL (baris odm Xiaomi menimpanya),
    # spesifikasi ingres DI AKHIR. Context file yang di-import = init (path bukan /vendor|/odm)
    local rel_p="/${pdir#"$sysroot"/}"
    for p in $OPLUS_PROP_ORDER; do
        in_list "$p" "$OPLUS_PRESENT" || continue
        [[ -f $sysroot/$p/build.prop ]] && imports+="import /$p/build.prop"$'\n'
    done
    [[ -f $pdir/port_oplus_odm.prop ]] && imports+="import $rel_p/port_oplus_odm.prop"$'\n'
    tail="import $rel_p/port_ingres.prop"
    mkdir -p "$(dirname "$odm_prop")"
    touch "$odm_prop"
    sed -i '/\[port-oplus\]/,/\[\/port-oplus\]/d' "$odm_prop"
    {
        printf '# [port-oplus] props partisi my_* OPlus (digabung ke /system)\n%s# [/port-oplus]\n' "$imports"
        cat "$odm_prop"
        printf '# [port-oplus] spesifikasi ingres\n%s\n# [/port-oplus]\n' "$tail"
    } > "$odm_prop.new"
    mv -f "$odm_prop.new" "$odm_prop"
    ok "OPlus props: $(grep -cE '^import /my_[a-z_]+/build\.prop$' "$odm_prop") build.prop my_* di-import dari odm/etc/build.prop; nama $OPLUS_MARKET_NAME, density ${dens:-donor}"
}

# passwd/group vendor OPlus (AID layanan OPlus) digabung ke vendor Xiaomi
oplus_ids() {
    local f res
    for f in passwd group; do
        [[ -f $OPLUS_DIR/vendor/etc/$f ]] || continue
        if [[ ! -f $B_FS/vendor/etc/$f ]]; then
            cp "$OPLUS_DIR/vendor/etc/$f" "$B_FS/vendor/etc/$f"; log "  vendor/etc/$f dari donor (base tidak punya)"; continue
        fi
        res=$(python3 "$SCRIPT_DIR/oplus_tool.py" merge-ids "$B_FS/vendor/etc/$f" "$OPLUS_DIR/vendor/etc/$f")
        log "  $res"
    done
}

# flag fitur OPlus (odm/etc/permissions) dari odm donor yang belum ada di odm base
oplus_features() {
    local src="$OPLUS_DIR/odm/etc/permissions" dst="$B_FS/odm/etc/permissions" f n=0
    [[ -d $src ]] || return 0
    mkdir -p "$dst"
    while IFS= read -r -d '' f; do
        [[ -e $dst/$(basename "$f") ]] && continue
        cp "$f" "$dst/"; n=$((n + 1))
    done < <(find "$src" -maxdepth 1 -type f -iname '*oplus*.xml' -print0)
    ok "OPlus: $n file flag fitur OPlus (odm/etc/permissions) dari odm donor"
}

# /mnt/vendor/my_* (path asli OPlus) = bind dari /my_* saat early-init
oplus_mnt_rc() {
    local rc="$P_FS/system/system/etc/init/port_oplus_my.rc" p
    mkdir -p "$(dirname "$rc")"
    {
        echo "# [port-oplus] partisi my_* digabung ke /system: path OPlus /mnt/vendor/my_* dibuat ulang"
        echo "on early-init"
        for p in $OPLUS_PRESENT; do
            echo "    mkdir /mnt/vendor/$p 0755 root root"
            echo "    mount none /$p /mnt/vendor/$p bind"
        done
    } > "$rc"
    ok "OPlus: /mnt/vendor/my_* di-bind dari /my_* saat early-init (system/etc/init/port_oplus_my.rc)"
}

oplus_debloat() {
    local line base n=0 d
    is_true "$OPLUS_DEBLOAT" || { log "OPlus debloat: OPLUS_DEBLOAT=false, dilewati"; return 0; }
    [[ -f $OPLUS_DEBLOAT_FILE ]] || { warn "OPlus debloat: $OPLUS_DEBLOAT_FILE tidak ada"; return 0; }
    shopt -s nullglob
    while IFS= read -r line || [[ -n $line ]]; do
        line=${line%%#*}; line=${line//[[:space:]]/}
        [[ -n $line ]] || continue
        case $line in my_*) base="$P_FS/system" ;; *) base="$P_FS" ;; esac
        # shellcheck disable=SC2231  # pola glob dari debloat_oplus.txt sengaja di-expand
        for d in $base/$line; do
            [[ -e $d ]] || continue
            is_kept "$(basename "$d")" && { log "  debloat: ${d#"$P_FS"/} dipertahankan (keep)"; continue; }
            rm -rf "$d"; n=$((n + 1)); log "  debloat: ${d#"$P_FS"/}"
        done
    done < "$OPLUS_DEBLOAT_FILE"
    shopt -u nullglob
    ok "OPlus debloat: $n path dibuang ($(basename "$OPLUS_DEBLOAT_FILE"); tambah sendiri lewat input debloat)"
}

oplus_patch() {
    [[ $PORT_FLAVOR == oplus ]] || return 0
    oplus_props
    oplus_ids
    oplus_features
    oplus_mnt_rc
    oplus_debloat
    rm -rf "${OPLUS_DIR:?}"
}

oplus_rom_identity() {
    python3 "$SCRIPT_DIR/oplus_tool.py" identity "$P_FS"
}
