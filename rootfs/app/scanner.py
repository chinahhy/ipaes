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

import os, sys, json, zipfile, plistlib, shutil
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse, quote

# 轻松签/全能签/魔力签类软件源锁定下载字段：
# - apps[].isNeedlock=1 后客户端按钮显示“解锁”
# - news.url 提供解锁验证接口；news.key 与接口返回 md5(key+udid) 配套
LOCK_AUTH_KEY = os.environ.get("IPA_LOCK_AUTH_KEY", "hoya_ipa_lock_v1")
# 服务端真实鉴权 token：写进订阅 URL 和 IPA 下载 URL；没有 token 的请求由 nginx 拦截。
ACCESS_TOKEN = os.environ.get("IPA_ACCESS_TOKEN", "").strip()

def with_access_token(url: str) -> str:
    """给 repo.json 里的下载链接追加 token 参数；token 值不写入日志。"""
    if not ACCESS_TOKEN:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}token={quote(ACCESS_TOKEN)}"

# ===== 配置（全部来自环境变量）=====
BASE_URL = os.environ.get("REPO_BASE_URL", "https://example.com/repo")
REPO_NAME = os.environ.get("REPO_NAME", "Private IPA Repo")
REPO_IDENTIFIER = os.environ.get("REPO_IDENTIFIER", "com.private.ipa.repo")

# 从 BASE_URL 提取路径部分作为 repo 目录名
# e.g. https://ipa.example.com/x7k9m2hP → repo_dir = x7k9m2hP
_parsed = urlparse(BASE_URL.rstrip("/"))
_path_parts = [p for p in _parsed.path.split("/") if p]
REPO_DIR_NAME = _path_parts[-1] if _path_parts else "repo"

DATA_DIR = Path("/data")
IPA_DIR = DATA_DIR / "ipa"
ICONS_DIR = DATA_DIR / "icons"
REPO_JSON = DATA_DIR / "repo.json"
CACHE_DB = DATA_DIR / ".scan_cache.json"
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

def extract_largest_icon(zf, app_dir, plist, out_path):
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

    art = app_prefix + "iTunesArtwork"
    if art in zf.namelist():
        try:
            size = zf.getinfo(art).file_size
            candidates.append((0, -size, art))
        except: pass

    if not candidates: return False
    candidates.sort()
    _, _, icon_name = candidates[0]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(icon_name) as src, open(out_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return True

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

def build_app_entry(meta: dict) -> dict:
    ipa_url = with_access_token(f"{BASE_URL}/ipa/{quote(meta['ipa_filename'])}")
    icon_url = f"{BASE_URL}/icons/{quote(meta['icon_filename'])}?v={int(meta['mtime'])}" if meta.get("icon_filename") else ""
    date_str = iso_date(meta["mtime"])

    version_entry = {
        "version": meta["version"], "date": date_str,
        "localizedDescription": f"From {meta['ipa_filename']}",
        "downloadURL": ipa_url, "size": meta["size"],
        "minOSVersion": meta["minOSVersion"], "maxOSVersion": "99.0",
        "isNeedlock": 0, "appType": 1,
    }

    return {
        "name": meta["name"], "bundleIdentifier": meta["bundleIdentifier"],
        "developerName": "Private Repo", "subtitle": meta["name"],
        "localizedDescription": f"Auto-extracted from {meta['ipa_filename']}",
        "iconURL": icon_url, "tintColor": "3478F6", "screenshotURLs": [], "beta": False,
        "versions": [version_entry],
        "version": meta["version"], "versionDate": date_str,
        "versionDescription": f"From {meta['ipa_filename']}",
        "downloadURL": ipa_url, "size": meta["size"],
        "isNeedlock": 0, "appType": 1,
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
        new_cache[ipa.name] = {"sig": sig}
        if cache.get(ipa.name, {}).get("sig") == sig and "meta" in cache[ipa.name]:
            meta = cache[ipa.name]["meta"]
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

    # 仓库元信息（顶层 iconURL 是 AltStore/Esign 协议字段，让客户端订阅源时能显示 logo）
    # _repo.png 是一张固定文件，不参与 IPA 扫描清理（见下方 valid_icons 集合明确放行）
    repo = {
        "name": REPO_NAME,
        "identifier": REPO_IDENTIFIER,
        "iconURL": f"{BASE_URL}/icons/_repo.png",
        "apps": final_apps,
        "news": {
            "title": REPO_NAME,
            "caption": "此源使用专属 token URL 访问；请勿外传订阅链接。",
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "key": LOCK_AUTH_KEY,
            "tintColor": "3478F6",
            "isUnlock": 0,
            "imageURL": f"{BASE_URL}/icons/_repo.png",
            "url": "",
            "pay": "",
        },
    }
    REPO_JSON.write_text(json.dumps(repo, indent=2, ensure_ascii=False))
    save_cache(new_cache)

    # 清理孤立图标：删掉那些不对应任何已知 IPA 的图标
    # 但要放行 _ 开头的元数据文件（_repo.png 等仓库级图标），它们不属于 IPA 但要保留
    valid_icons = {m["meta"].get("icon_filename") for m in new_cache.values() if m.get("meta", {}).get("icon_filename")}
    for icon in ICONS_DIR.glob("*.png"):
        if icon.name.startswith("_"):
            continue  # _repo.png 等元数据图标永不清理
        if icon.name not in valid_icons:
            print(f"  🗑️ 清理孤立图标: {icon.name}")
            icon.unlink()

    print(f"\n✅ 完成！合并后 {len(final_apps)} 个app（IPA文件{len(apps)}个）")
    return len(apps)

if __name__ == "__main__":
    scan()
