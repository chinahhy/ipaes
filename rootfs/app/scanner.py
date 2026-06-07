#!/usr/bin/env python3
"""
IPA仓库自动扫描器（AltStore/Esign兼容格式）
扫描 /data/ipa/ → 提取元数据 → 生成 /data/repo.json

所有配置通过环境变量控制，零硬编码：
  REPO_BASE_URL  - 源的公网URL（如 https://ipa.example.com/myrepo）
  REPO_NAME      - 源名称
  REPO_IDENTIFIER- 源bundleId
  REPO_PATH      - URL路径部分（默认从BASE_URL提取）
"""

import os, sys, json, zipfile, plistlib, shutil, struct, zlib, io, binascii
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse, quote

# 客户端解锁码（Esign / 全能签 UI 锁）：
# - apps[].isNeedlock=1 → 客户端默认显示“解锁”按钮，点击后弹解锁码输入框
# - news.isUnlock=1 + news.url 指向 /auth → 客户端把 udid+code POST 到 /auth 校验
# - news.key 是服务端用来生成解锁凭证的密钥（仅 UI 锁，非真实下载鉴权）
LOCK_AUTH_KEY = os.environ.get("IPA_LOCK_AUTH_KEY", "hoya_ipa_lock_v1")
# 历史遗留：以前 IPA 直链带 ?token= 做 nginx 层鉴权。现在订阅链接公开分享，
# 下载 URL 不再附 token，nginx 也不再拦截。订阅根路径 /<REPO_PATH>/ 仍是随机
# 字符串，防止陌生扫描器拉到 repo.json。
def with_access_token(url: str) -> str:
    return url

# ===== 配置（全部来自环境变量）=====
BASE_URL = os.environ.get("REPO_BASE_URL", "https://example.com/repo").rstrip("/")
REPO_NAME = os.environ.get("REPO_NAME", "Private IPA Repo")
REPO_IDENTIFIER = os.environ.get("REPO_IDENTIFIER", "com.private.ipa.repo")

# 解锁码（unlock.json.code）不再参与 URL 路径生成，URL 与解锁码彻底解耦。
# REPO_BASE_URL 现在直接就是对外发布的订阅前缀（可以是 https://domain 或 https://domain/segment）。
_parsed = urlparse(BASE_URL.rstrip("/"))
_path_parts = [p for p in _parsed.path.split("/") if p]
REPO_DIR_NAME = _path_parts[-1] if _path_parts else "repo"

DATA_DIR = Path("/data")
IPA_DIR = DATA_DIR / "ipa"
ICONS_DIR = DATA_DIR / "icons"
REPO_JSON = DATA_DIR / "repo.json"
CACHE_DB = DATA_DIR / ".scan_cache.json"
ICON_EXTRACTOR_VERSION = "cgbi-v3-flutter-deep"
# ================

def load_cache():
    if CACHE_DB.exists():
        try: return json.loads(CACHE_DB.read_text())
        except: return {}
    return {}

def save_cache(cache):
    CACHE_DB.write_text(json.dumps(cache, indent=2, ensure_ascii=False))

def file_signature(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}-{int(st.st_mtime)}"

def iso_date(epoch: float) -> str:
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")

def find_app_dir_in_ipa(zf):
    for name in zf.namelist():
        parts = name.split("/")
        if len(parts) >= 3 and parts[0] == "Payload" and parts[1].endswith(".app") and parts[2] == "Info.plist":
            return parts[1]
    return None



PNG_SIG = bytes([137, 80, 78, 71, 13, 10, 26, 10])


def _png_chunk(ctype: bytes, cdata: bytes) -> bytes:
    return (
        struct.pack('>I', len(cdata)) +
        ctype +
        cdata +
        struct.pack('>I', binascii.crc32(ctype + cdata) & 0xffffffff)
    )


def _png_chunks(data: bytes):
    if data[:8] != PNG_SIG:
        raise ValueError("not a PNG")
    pos = 8
    while pos + 12 <= len(data):
        length = struct.unpack('>I', data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        end = pos + 8 + length
        if end + 4 > len(data):
            raise ValueError("truncated PNG chunk")
        cdata = data[pos + 8:end]
        yield ctype, cdata
        pos = end + 4
        if ctype == b'IEND':
            return
    raise ValueError("PNG missing IEND")


def _bytes_per_pixel(color_type: int, bit_depth: int) -> int:
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise ValueError("unsupported PNG color type")
    return max(1, (channels * bit_depth + 7) // 8)


def _row_bytes(width: int, color_type: int, bit_depth: int) -> int:
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise ValueError("unsupported PNG color type")
    return (width * channels * bit_depth + 7) // 8


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter_png_rows(raw: bytes, width: int, height: int, color_type: int, bit_depth: int) -> list[bytearray]:
    bpp = _bytes_per_pixel(color_type, bit_depth)
    row_len = _row_bytes(width, color_type, bit_depth)
    expected = height * (row_len + 1)
    if len(raw) != expected:
        raise ValueError("unexpected PNG scanline length")

    rows = []
    prev = bytearray(row_len)
    pos = 0
    for _ in range(height):
        filter_type = raw[pos]
        pos += 1
        src = raw[pos:pos + row_len]
        pos += row_len
        row = bytearray(row_len)

        if filter_type == 0:
            row[:] = src
        elif filter_type == 1:
            for i, value in enumerate(src):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (value + left) & 0xff
        elif filter_type == 2:
            for i, value in enumerate(src):
                row[i] = (value + prev[i]) & 0xff
        elif filter_type == 3:
            for i, value in enumerate(src):
                left = row[i - bpp] if i >= bpp else 0
                up = prev[i]
                row[i] = (value + ((left + up) // 2)) & 0xff
        elif filter_type == 4:
            for i, value in enumerate(src):
                left = row[i - bpp] if i >= bpp else 0
                up = prev[i]
                up_left = prev[i - bpp] if i >= bpp else 0
                row[i] = (value + _paeth(left, up, up_left)) & 0xff
        else:
            raise ValueError("unknown PNG filter")

        rows.append(row)
        prev = row
    return rows


def _encode_unfiltered_rows(rows: list[bytearray]) -> bytes:
    out = bytearray()
    for row in rows:
        out.append(0)
        out.extend(row)
    return bytes(out)


def _standard_png_ok(data: bytes) -> bool:
    try:
        chunks = list(_png_chunks(data))
        if any(ctype == b'CgBI' for ctype, _ in chunks):
            return False
        ihdr = next(cdata for ctype, cdata in chunks if ctype == b'IHDR')
        width, height = struct.unpack('>II', ihdr[:8])
        bit_depth = ihdr[8]
        color_type = ihdr[9]
        interlace = ihdr[12]
        idat = b''.join(cdata for ctype, cdata in chunks if ctype == b'IDAT')
        raw = zlib.decompress(idat)
        if interlace:
            return bool(raw)
        row_len = _row_bytes(width, color_type, bit_depth)
        return len(raw) == height * (row_len + 1)
    except Exception:
        return False


def _cgbi_to_png(data: bytes) -> bytes:
    """Convert Apple CgBI PNG bytes to standard PNG bytes.
    CgBI stores IDAT as raw deflate (no zlib wrapper) with BGR/BGRA pixel order.
    We decompress raw deflate, swap B<->R channels for color types 2 & 6,
    recompress with standard zlib, and remove the CgBI chunk.
    Non-CgBI data is returned unchanged (identity check safe)."""
    if data[:8] != PNG_SIG:
        return data
    # Precise: CgBI replaces the normal IHDR chunk position at bytes 12-15
    if data[12:16] != b'CgBI':
        return data

    ihdr = None
    idat_parts = bytearray()
    other_chunks = []  # (ctype, cdata) in original order

    for ctype, cdata in _png_chunks(data):
        if ctype == b'CgBI':
            continue
        elif ctype == b'IHDR':
            ihdr = cdata
            other_chunks.append((ctype, cdata))
        elif ctype == b'IDAT':
            idat_parts.extend(cdata)
        elif ctype == b'IEND':
            other_chunks.append((ctype, cdata))
        else:
            other_chunks.append((ctype, cdata))

    if not ihdr or not idat_parts:
        return data  # malformed, return as-is

    # Parse IHDR for dimensions and color info
    width = struct.unpack('>I', ihdr[0:4])[0]
    height = struct.unpack('>I', ihdr[4:8])[0]
    bit_depth = ihdr[8]
    color_type = ihdr[9]

    # Decompress all IDAT as raw deflate (no zlib wrapper, wbits=-15)
    try:
        raw_rows = zlib.decompress(bytes(idat_parts), -15)
    except Exception:
        return data

    # BGR/BGRA -> RGB/RGBA swap for color types 2 (RGB) and 6 (RGBA)
    if color_type in (2, 6) and bit_depth == 8:
        rows = _unfilter_png_rows(raw_rows, width, height, color_type, bit_depth)
        bpp = 3 if color_type == 2 else 4
        for row in rows:
            for px in range(0, len(row), bpp):
                blue, green, red = row[px], row[px + 1], row[px + 2]
                if color_type == 6:
                    alpha = row[px + 3]
                    if 0 < alpha < 255:
                        red = min(255, round(red * 255 / alpha))
                        green = min(255, round(green * 255 / alpha))
                        blue = min(255, round(blue * 255 / alpha))
                    row[px:px + 4] = bytes((red, green, blue, alpha))
                else:
                    row[px:px + 3] = bytes((red, green, blue))
        raw_rows = _encode_unfiltered_rows(rows)

    # Recompress with standard zlib wrapper
    try:
        new_idat = zlib.compress(raw_rows)
    except Exception:
        return data

    # Reconstruct standard PNG, inserting new IDAT before IEND
    out = io.BytesIO()
    out.write(PNG_SIG)
    for ctype, cdata in other_chunks:
        if ctype == b'IEND':
            out.write(_png_chunk(b'IDAT', new_idat))
        if ctype != b'iDOT':
            out.write(_png_chunk(ctype, cdata))
    return out.getvalue()


def _normalize_png(path):
    """Normalize PNG icon to standard format.
    Standard PNGs pass through, CgBI PNGs converted in-place."""
    try:
        raw = open(path, 'rb').read()
        converted = _cgbi_to_png(raw)
        if converted is not raw:
            open(path, 'wb').write(converted)
            raw = converted
        if not _standard_png_ok(raw):
            raise ValueError("invalid PNG icon")
        return True
    except (OSError, struct.error, zlib.error, binascii.Error):
        # CgBI conversion or file write failed -- remove partial file
        try:
            os.remove(path)
        except OSError:
            pass
        return False
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        return False

def extract_largest_icon(zf, app_dir, plist, out_path):
    """Extract best app icon from IPA. All PNGs accepted - CgBI auto-converted."""
    declared_names = set()
    icons_dict = plist.get("CFBundleIcons") or {}
    primary = icons_dict.get("CFBundlePrimaryIcon") or {}
    for f in primary.get("CFBundleIconFiles", []) or []:
        declared_names.add(f)
    for f in plist.get("CFBundleIconFiles", []) or []:
        declared_names.add(f)

    app_prefix = f"Payload/{app_dir}/"
    candidates = []

    for name in zf.namelist():
        if not name.startswith(app_prefix): continue
        lower = name.lower()
        if not lower.endswith(".png"): continue
        basename = name[len(app_prefix):]
        if "/" in basename: continue
        stem = basename.rsplit(".", 1)[0]
        stem_clean = stem.rsplit("@", 1)[0]
        try: size = zf.getinfo(name).file_size
        except: continue

        if stem_clean in declared_names or stem in declared_names:
            candidates.append((1, -size, name)); continue
        if stem_clean.startswith("AppIcon") or stem.startswith("AppIcon"):
            candidates.append((2, -size, name)); continue
        if stem_clean.startswith("Icon-") or stem_clean.startswith("Icon") or stem_clean == "iTunesArtwork":
            candidates.append((3, -size, name))

    # Pass 2: Flutter / framework icons at common deep paths
    # These are standard PNGs (not CgBI), often higher resolution than root-level icons
    flutter_paths = [
        f"{app_prefix}Frameworks/App.framework/flutter_assets/assets/images/icon.png",
        f"{app_prefix}Frameworks/App.framework/flutter_assets/Icon-1024.png",
        f"{app_prefix}Frameworks/App.framework/flutter_assets/icon.png",
        f"{app_prefix}Frameworks/App.framework/flutter_assets/AppIcon.png",
    ]
    for fpath in flutter_paths:
        if fpath in zf.namelist():
            try:
                size = zf.getinfo(fpath).file_size
                candidates.append((5, -size, fpath))
            except Exception:
                pass

    # Pass 2b: Flutter / 通用深层图标 —— 按文件名兜底匹配。
    # 例如 PiliPlus 把图标放在 flutter_assets/assets/images/logo/logo.png，
    # 不在固定路径表里。这里扫遍所有 PNG，按文件名命中 logo/icon 关键字，
    # 并按尺寸取最大，避免漏掉 Flutter / RN / Cordova 等框架的非标准图标位置。
    deep_keywords = ("logo", "icon", "applogo", "app_logo", "appicon")
    seen_deep = set()
    for name in zf.namelist():
        if not name.startswith(app_prefix):
            continue
        lower = name.lower()
        if not lower.endswith(".png"):
            continue
        # 只看深层路径（根级 PNG 已在 Pass 1 处理）
        rel = name[len(app_prefix):]
        if "/" not in rel:
            continue
        if name in seen_deep:
            continue
        # 跳过明显不是 app 图标的（按钮、占位、动效素材等）
        skip_markers = ("button", "btn", "placeholder", "loading", "splash",
                        "background", "bg_", "/bg.", "banner", "/lv/", "/paycoins/",
                        "thumb", "avatar", "/img/", "/images/play", "/images/ai")
        if any(m in lower for m in skip_markers):
            continue
        stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        # 只接受 stem 本身就是 logo/icon 类关键词的，避免拿到 "logo_button" 这种
        if stem not in deep_keywords and not any(stem.endswith(k) or stem.startswith(k) for k in ("logo", "appicon", "app_icon")):
            continue
        try:
            size = zf.getinfo(name).file_size
        except Exception:
            continue
        # 太小（< 4KB）多半不是合格图标
        if size < 4096:
            continue
        # 深层 logo/icon ≥ 16KB 时，明显比根级 AppIcon 占位图更合适，
        # 提到 priority=1（仅次于 CFBundleIconFiles 显式声明）。
        # < 16KB 但 ≥ 4KB 时只作为兜底（priority=4）。
        prio = 1 if size >= 16384 else 4
        candidates.append((prio, -size, name))
        seen_deep.add(name)

    # iTunesArtwork (no .png extension) -- highest priority, fallback
    for art_name in (f"{app_prefix}iTunesArtwork", "iTunesArtwork"):
        if art_name in zf.namelist():
            try:
                size = zf.getinfo(art_name).file_size
                candidates.append((0, -size, art_name))
            except Exception:
                pass

    if not candidates: return False
    candidates.sort()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    for _, _, icon_name in candidates:
        with zf.open(icon_name) as src, open(out_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        if _normalize_png(out_path):
            return True
        out_path.unlink(missing_ok=True)
    
    return False

def parse_ipa(ipa_path: Path):
    try:
        with zipfile.ZipFile(ipa_path, "r") as zf:
            app_dir = find_app_dir_in_ipa(zf)
            if not app_dir:
                print(f"  ⚠️ 找不到.app目录: {ipa_path.name}")
                return None
            with zf.open(f"Payload/{app_dir}/Info.plist") as f:
                plist = plistlib.load(f)

            bundle_id = plist.get("CFBundleIdentifier", "unknown.bundle.id")
            version = str(plist.get("CFBundleShortVersionString") or plist.get("CFBundleVersion", "1.0"))
            app_name = plist.get("CFBundleDisplayName") or plist.get("CFBundleName") or app_dir.replace(".app", "")
            min_ios = str(plist.get("MinimumOSVersion", "12.0"))

            icon_filename = f"{bundle_id}.png"
            icon_path = ICONS_DIR / icon_filename
            # 用户手动覆盖：若 icons/<bundle>.user.png 存在，则把它复制到 <bundle>.png 并跳过自动提取。
            # 这是解决 IPA 内部图标错乱（活动期间皮肤、广告图标等）的兜底手段。
            user_icon = ICONS_DIR / f"{bundle_id}.user.png"
            if user_icon.exists() and _standard_png_ok(user_icon.read_bytes()):
                try:
                    icon_path.write_bytes(user_icon.read_bytes())
                    icon_ok = True
                    print(f"  🖼️ 使用用户自定义图标: {bundle_id}.user.png")
                except Exception as e:
                    print(f"  ⚠️ user.png 复制失败 {bundle_id}: {e}")
                    icon_ok = extract_largest_icon(zf, app_dir, plist, icon_path)
            else:
                icon_ok = extract_largest_icon(zf, app_dir, plist, icon_path)

            return {
                "name": app_name, "bundleIdentifier": bundle_id, "version": version,
                "size": ipa_path.stat().st_size, "minOSVersion": min_ios,
                "ipa_filename": ipa_path.name, "icon_filename": icon_filename if icon_ok else None,
                "mtime": ipa_path.stat().st_mtime,
            }
    except Exception as e:
        print(f"  ❌ 解析失败 {ipa_path.name}: {e}")
        return None

def cached_meta_usable(meta: dict) -> bool:
    icon_filename = meta.get("icon_filename")
    if not icon_filename:
        return True
    icon_path = ICONS_DIR / icon_filename
    try:
        return icon_path.exists() and _standard_png_ok(icon_path.read_bytes())
    except Exception:
        return False

def build_app_entry(meta: dict) -> dict:
    ipa_url = with_access_token(f"{BASE_URL}/ipa/{quote(meta['ipa_filename'])}")
    # 版本号绑定到图标文件本身的 mtime + size，这样图标重新提取后 URL 一定变化，
    # 避免客户端（轻松签 / 全能签）按 URL 缓存旧的占位图。
    icon_url = ""
    if meta.get("icon_filename"):
        icon_path = ICONS_DIR / meta["icon_filename"]
        try:
            st = icon_path.stat()
            icon_ver = f"{int(st.st_mtime)}-{st.st_size}"
        except OSError:
            icon_ver = str(int(meta["mtime"]))
        icon_url = f"{BASE_URL}/icons/{quote(meta['icon_filename'])}?v={icon_ver}"
    date_str = iso_date(meta["mtime"])
    # 严格按魔力签/全能签源协议组织字段顺序：
    # name → versionDate → version → iconURL → downloadURL → size → isNeedlock → appType → localizedDescription
    # size 在魔力签规范里是字符串（如 "58M"），不是字节数；这里转成 MB 字符串。
    size_mb = max(1, round(meta["size"] / (1024 * 1024)))
    size_str = f"{size_mb}M"
    # localizedDescription 不再用 "Auto-extracted from xxx.ipa" 这种文件名露出（用户要求公开仓库时干净）。
    desc = f"{meta['name']} v{meta['version']}"
    # 全能签源协议（参照 CN-CodeMan/AppStore/App.json 这个实际部署的源）。
    # 注：仓库主人决定不启用解锁按钮（自用源），所以 lock 固定 "0" = 免费下载。
    # 如果以后要恢复解锁机制，把 lock 改成 "1" 并提供 unlockURL 即可。
    return {
        "name": meta["name"],
        "version": meta["version"],
        "versionDate": date_str,
        "versionDescription": desc,
        "lock": "0",
        "downloadURL": ipa_url,
        "isLanZouCloud": "0",
        "iconURL": icon_url,
        "tintColor": "",
        "size": str(meta["size"]),
    }

def _safe_mkdir(p: Path):
    """兼容软链接的目录创建"""
    if p.exists() or p.is_symlink():
        return
    p.mkdir(parents=True, exist_ok=True)

def scan():
    print(f"🔍 扫描目录: {IPA_DIR}")
    _safe_mkdir(IPA_DIR)
    _safe_mkdir(ICONS_DIR)

    cache = load_cache()
    new_cache = {}
    apps = []

    import time as _time
    _now = _time.time()
    _all_ipas = list(IPA_DIR.glob("*.ipa"))
    _skipped = []
    ipa_files = []
    for _f in sorted(_all_ipas):
        try: _st = _f.stat()
        except FileNotFoundError: continue
        _age = _now - _st.st_mtime
        if _age < 5:
            _skipped.append(f"{_f.name} (mtime太新 {_age:.1f}s)"); continue
        if _st.st_size < 1024*1024:
            _skipped.append(f"{_f.name} (太小 {_st.st_size}B)"); continue
        ipa_files.append(_f)
    if _skipped:
        print(f"⏳ 跳过{len(_skipped)}个未就绪文件: " + ", ".join(_skipped[:5]))
    print(f"📦 发现 {len(ipa_files)} 个IPA文件")

    for ipa in ipa_files:
        sig = file_signature(ipa)
        new_cache[ipa.name] = {"sig": sig, "icon_extractor": ICON_EXTRACTOR_VERSION}
        cached = cache.get(ipa.name, {})
        if (
            cached.get("sig") == sig and
            cached.get("icon_extractor") == ICON_EXTRACTOR_VERSION and
            "meta" in cached and
            cached_meta_usable(cached["meta"])
        ):
            meta = cached["meta"]
            new_cache[ipa.name]["meta"] = meta
            print(f"  ✓ 缓存命中: {ipa.name}")
        else:
            print(f"  🔧 解析: {ipa.name}")
            meta = parse_ipa(ipa)
            if not meta: continue
            new_cache[ipa.name]["meta"] = meta
        apps.append(meta)

    def ver_cmp(v):
        try: return tuple(int(x) for x in v.split("."))
        except: return (0,)

    by_bundle = {}
    for m in apps:
        bid = m.get("bundleIdentifier")
        if not bid: continue
        prev = by_bundle.get(bid)
        if prev is None:
            by_bundle[bid] = m
        else:
            if ver_cmp(m["version"]) > ver_cmp(prev["version"]):
                by_bundle[bid] = m
            elif ver_cmp(m["version"]) == ver_cmp(prev["version"]) and m["mtime"] > prev["mtime"]:
                by_bundle[bid] = m

    sorted_metas = sorted(by_bundle.values(), key=lambda x: (-x["mtime"], x.get("bundleIdentifier", "")))
    final_apps = [build_app_entry(m) for m in sorted_metas]
    print(f"📊 合并: 总IPA{len(apps)}个 → 去重后{len(final_apps)}个app（按mtime倒序）")

    # ---- 顶层结构：全能签源协议（参照 CN-CodeMan/AppStore App.json 实际部署案例）----
    # 全能签客户端期望的字段：name/message/identifier/sourceURL/sourceicon/payURL/unlockURL/apps
    # 解锁地址在顶层 unlockURL，apps[].lock = "1" 代表加锁。
    repo = {
        "name": REPO_NAME,
        "message": "添加源后请向源主索取解锁码后方可下载。",
        "identifier": REPO_IDENTIFIER,
        "sourceURL": BASE_URL,
        "sourceicon": f"{BASE_URL}/icons/_repo.png",
        "payURL": "",
        "unlockURL": f"{BASE_URL}/auth",
        "apps": final_apps,
    }
    REPO_JSON.write_text(json.dumps(repo, indent=2, ensure_ascii=False))

    # AltStore / Sideloadly 兼容副本：供需要 AltStore 协议的客户端单独订阅
    altstore_repo = {
        "name": REPO_NAME,
        "identifier": REPO_IDENTIFIER,
        "iconURL": f"{BASE_URL}/icons/_repo.png",
        "apps": final_apps,
    }
    try:
        (REPO_JSON.parent / "_altstore.json").write_text(
            json.dumps(altstore_repo, indent=2, ensure_ascii=False)
        )
    except Exception as e:
        print(f"⚠️ 写 _altstore.json 失败: {e}")
    save_cache(new_cache)

    # 清理孤立图标：删掉那些不对应任何已知 IPA 的图标
    # 但要放行 _ 开头的元数据文件（_repo.png 等仓库级图标），它们不属于 IPA 但要保留
    valid_icons = {m["meta"].get("icon_filename") for m in new_cache.values() if m.get("meta", {}).get("icon_filename")}
    for icon in ICONS_DIR.glob("*.png"):
        if icon.name.startswith("_"):
            continue  # _repo.png 等元数据图标永不清理
        if icon.name.endswith(".user.png"):
            continue  # 用户手动放置的覆盖图标永不清理
        if icon.name not in valid_icons:
            print(f"  🗑️ 清理孤立图标: {icon.name}")
            icon.unlink()

    print(f"\n✅ 完成！合并后 {len(final_apps)} 个app（IPA文件{len(apps)}个）")
    return len(apps)

if __name__ == "__main__":
    scan()
