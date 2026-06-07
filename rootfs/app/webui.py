#!/usr/bin/env python3
# IPA Self-Host WebUI backend (Flask)
# Cookie 登录 + App 卡片首页 + 删除访问控制 tab
import os, sys, json, shutil, subprocess, time, hashlib, secrets, re, zipfile
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps
from urllib.parse import urlparse
from flask import Flask, request, jsonify, send_from_directory, Response, abort, make_response, redirect

CONFIG_PATH = Path("/config/config.json")
STATE_PATH = Path("/config/state.json")
IPA_DIR = Path("/data/ipa")
ICONS_DIR = Path("/data/icons")
LOG_DIR = Path("/logs")
FORWARD_BOT_CONFIG_PATH = Path("/config/forward_bot.json")
SCAN_SCRIPT = "/app/run-tg-scan.sh"
SCANNER_SCRIPT = "/app/scanner.py"
STATIC_DIR = Path(__file__).parent / "webui_static"

# 受保护文件：禁止删除/移除，仅允许下载
PROTECTED_FILES = {"X_10.76_证书安装登录版本.ipa"}

AUTH_PATH = Path("/config/webui_auth.json")
RESET_PATH = Path("/config/webui_reset.json")
RESET_LOG_PATH = LOG_DIR / "webui-reset.log"
RESET_CODE_TTL = 10 * 60
# 账号来源优先级：/config/webui_auth.json > 旧 webui_pass.json/env > 默认 admin/admin
UI_USER = os.environ.get("WEBUI_USER", "admin")
PASS_PATH = Path("/config/webui_pass.json")
_ENV_PASS = os.environ.get("WEBUI" + "_" + "PASS" + "WORD", "admin")

# Session 存储：{session_id: {"user": str, "expires": float}}
# 生产环境可用 Redis/文件持久化，这里用内存简单实现（容器重启需重新登录）
_sessions = {}
SESSION_MAX_AGE = 86400 * 7  # 7 天

def _read_auth():
    auth = {"username": UI_USER, "password": _ENV_PASS}
    if PASS_PATH.exists():
        try:
            auth["password"] = json.loads(PASS_PATH.read_text()).get("password") or auth["password"]
        except Exception:
            pass
    if AUTH_PATH.exists():
        try:
            data = json.loads(AUTH_PATH.read_text())
            auth["username"] = (data.get("username") or auth["username"]).strip()
            auth["password"] = data.get("password") or auth["password"]
        except Exception:
            pass
    return auth

def _read_username():
    return _read_auth()["username"]

def _read_password():
    return _read_auth()["password"]

def _write_auth(username=None, password=None):
    auth = _read_auth()
    if username is not None:
        auth["username"] = username
    if password is not None:
        auth["password"] = password
    AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_PATH.write_text(json.dumps(auth, ensure_ascii=False, indent=2))

def _write_password(new_pass):
    _write_auth(password=new_pass)

def _create_session(username):
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = {"user": username, "expires": time.time() + SESSION_MAX_AGE}
    return sid

def _valid_session(sid):
    s = _sessions.get(sid)
    if not s:
        return False
    if s["expires"] < time.time():
        _sessions.pop(sid, None)
        return False
    return True

def _clear_sessions():
    _sessions.clear()

app = Flask(__name__, static_folder=None)

# ============ 工具函数 ============
def load_config():
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

def save_config(cfg):
    if CONFIG_PATH.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = CONFIG_PATH.with_suffix(f".json.bak-{ts}")
        shutil.copy2(CONFIG_PATH, bak)
        baks = sorted(CONFIG_PATH.parent.glob("config.json.bak-*"))
        for old in baks[:-10]:
            old.unlink()
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )

def load_state():
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))

def save_state(state):
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2)
    )

# ============ 认证：Cookie 优先，Basic Auth 兼容 ============
def check_auth():
    """优先检查 Cookie session，其次 Basic Auth（兼容 curl 排障）"""
    # 1. Cookie
    sid = request.cookies.get("session_id")
    if sid and _valid_session(sid):
        return True
    # 2. Basic Auth（curl -u admin:admin 兼容）
    a = request.authorization
    if a and a.username == _read_username() and a.password == _read_password():
        return True
    return False

def require_auth(f):
    @wraps(f)
    def wrapper(*a, **k):
        if not check_auth():
            # API 请求返回 JSON 401，不带 WWW-Authenticate（避免浏览器弹框）
            if request.path.startswith("/api/"):
                return jsonify({"error": "未登录"}), 401
            # 页面请求跳转登录
            return redirect("/login")
        return f(*a, **k)
    return wrapper

# ============ 登录/登出 ============
@app.route("/login", methods=["GET"])
def login_page():
    return send_from_directory(STATIC_DIR, "login.html")

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    if username != _read_username() or password != _read_password():
        return jsonify({"error": "用户名或密码错误"}), 401
    sid = _create_session(username)
    resp = make_response(jsonify({"ok": True}))
    resp.set_cookie("session_id", sid, max_age=SESSION_MAX_AGE,
                    httponly=True, samesite="Lax")
    return resp

@app.route("/api/logout", methods=["POST"])
def api_logout():
    sid = request.cookies.get("session_id")
    if sid:
        _sessions.pop(sid, None)
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie("session_id")
    return resp

# ============ API: 配置 ============
@app.route("/api/config", methods=["GET"])
@require_auth
def api_get_config():
    cfg = load_config()
    safe = {
        "groups": cfg.get("groups", []),
        "priority_groups": cfg.get("priority_groups", []),
        "whitelist": cfg.get("whitelist", []),
        "rate_limit": cfg.get("rate_limit", {}),
    }
    return jsonify(safe)

@app.route("/api/groups", methods=["POST"])
@require_auth
def api_add_group():
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    if not url.startswith("https://t.me/"):
        return jsonify({"error": "URL 必须以 https://t.me/ 开头"}), 400
    cfg = load_config()
    cfg.setdefault("groups", [])
    if url in cfg["groups"]:
        return jsonify({"error": "群组已存在"}), 409
    cfg["groups"].append(url)
    save_config(cfg)
    return jsonify({"ok": True, "groups": cfg["groups"]})

@app.route("/api/groups", methods=["DELETE"])
@require_auth
def api_del_group():
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    cfg = load_config()
    cfg["groups"] = [g for g in cfg.get("groups", []) if g != url]
    cfg["priority_groups"] = [g for g in cfg.get("priority_groups", []) if g != url]
    save_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/groups/priority", methods=["POST"])
@require_auth
def api_toggle_priority():
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    cfg = load_config()
    cfg.setdefault("priority_groups", [])
    if url in cfg["priority_groups"]:
        cfg["priority_groups"].remove(url)
    elif url in cfg.get("groups", []):
        cfg["priority_groups"].append(url)
    else:
        return jsonify({"error": "群组不存在"}), 404
    save_config(cfg)
    return jsonify({"ok": True, "priority_groups": cfg["priority_groups"]})

# ============ API: 白名单 ============
@app.route("/api/whitelist", methods=["POST"])
@require_auth
def api_add_app():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    keywords = data.get("keywords") or []
    if not name:
        return jsonify({"error": "name 必填"}), 400
    if not isinstance(keywords, list) or not keywords:
        return jsonify({"error": "keywords 必须是非空数组"}), 400
    cfg = load_config()
    cfg.setdefault("whitelist", [])
    existing = [w for w in cfg["whitelist"] if w["name"] == name]
    if existing:
        kws = existing[0].setdefault("keywords", [])
        for k in keywords:
            if k not in kws:
                kws.append(k)
    else:
        cfg["whitelist"].append({"name": name, "keywords": list(keywords)})
    save_config(cfg)
    return jsonify({"ok": True, "whitelist": cfg["whitelist"]})

@app.route("/api/whitelist", methods=["PUT"])
@require_auth
def api_update_app():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    keywords = data.get("keywords")
    if not name or not isinstance(keywords, list):
        return jsonify({"error": "name + keywords[] 必填"}), 400
    cfg = load_config()
    found = False
    for w in cfg.get("whitelist", []):
        if w["name"] == name:
            w["keywords"] = list(keywords)
            found = True
            break
    if not found:
        return jsonify({"error": "App 不存在"}), 404
    save_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/whitelist", methods=["DELETE"])
@require_auth
def api_del_app():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    cfg = load_config()
    cfg["whitelist"] = [w for w in cfg.get("whitelist", []) if w["name"] != name]
    save_config(cfg)
    return jsonify({"ok": True})

# ============ API: 速率 ============
@app.route("/api/rate_limit", methods=["PUT"])
@require_auth
def api_set_rate():
    data = request.get_json(force=True) or {}
    cfg = load_config()
    rl = cfg.setdefault("rate_limit", {})
    for k in ("min_interval_sec", "max_interval_sec", "max_per_day"):
        if k in data:
            try:
                v = int(data[k])
                if v <= 0:
                    return jsonify({"error": f"{k} 必须 > 0"}), 400
                rl[k] = v
            except (TypeError, ValueError):
                return jsonify({"error": f"{k} 必须是整数"}), 400
    if rl.get("min_interval_sec", 0) > rl.get("max_interval_sec", 0):
        return jsonify({"error": "min_interval 不能大于 max_interval"}), 400
    save_config(cfg)
    return jsonify({"ok": True, "rate_limit": rl})

# ============ API: IPA 文件管理 ============
def _filename_meta(filename: str) -> dict:
    stem = filename[:-4] if filename.lower().endswith(".ipa") else filename
    patterns = (
        r"^(.+?)_(\d+(?:\.\d+)+|\d+)(?:_|$)",
        r"^(.+?)\((\d+(?:\.\d+)+|\d+)\)",
        r"^(.+?)[-_]v?(\d+(?:\.\d+)+|\d+)(?:[_-]|$)",
    )
    for pattern in patterns:
        m = re.match(pattern, stem, re.IGNORECASE)
        if m:
            return {"app_name": m.group(1).strip(" _-"), "version": m.group(2)}
    return {"app_name": stem, "version": ""}

@app.route("/api/ipa", methods=["GET"])
@require_auth
def api_list_ipa():
    """列出所有 IPA 文件。从 scanner 缓存 + 文件系统读取元信息，按 App 分组"""
    cache_path = IPA_DIR.parent / ".scan_cache.json"
    meta_by_name = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
            for fname, entry in cache.items():
                m = entry.get("meta") or {}
                meta_by_name[fname] = {
                    "app_name": m.get("name", ""),
                    "bundle_id": m.get("bundleIdentifier", ""),
                    "version": m.get("version", ""),
                    "icon": m.get("icon_filename"),
                }
        except Exception:
            pass
    # 加载白名单用于过滤展示（手动放入但不在白名单的 IPA 不展示）
    try:
        cfg = load_config()
        whitelist = cfg.get("whitelist") or []
    except Exception:
        whitelist = []

    def _in_whitelist(filename: str, app_name: str) -> bool:
        if not whitelist:
            return True
        haystack = f"{filename} {app_name}".lower()
        for app in whitelist:
            for kw in (app.get("keywords") or []):
                if str(kw).strip() and str(kw).strip().lower() in haystack:
                    return True
            if str(app.get("name") or "").strip().lower() in haystack:
                return True
        return False

    files = []
    if IPA_DIR.exists():
        for f in sorted(IPA_DIR.glob("*.ipa")):
            st = f.stat()
            m = meta_by_name.get(f.name, {})
            fallback = _filename_meta(f.name)
            meta_ok = bool(m.get("app_name") and m.get("bundle_id") and m.get("version"))
            is_zip = zipfile.is_zipfile(f)
            scan_status = "ok" if meta_ok else ("invalid" if not is_zip else "unscanned")
            app_name = m.get("app_name") or fallback["app_name"]
            if not _in_whitelist(f.name, app_name):
                continue
            files.append({
                "filename": f.name,
                "size": st.st_size,
                "size_mb": round(st.st_size / 1024 / 1024, 1),
                "mtime": int(st.st_mtime),
                "mtime_str": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "app_name": app_name,
                "bundle_id": m.get("bundle_id", ""),
                "version": m.get("version") or fallback["version"],
                "icon": m.get("icon"),
                "scan_status": scan_status,
                "downloadable": scan_status != "invalid",
                "protected": f.name in PROTECTED_FILES,
            })
    files.sort(key=lambda x: -x["mtime"])
    return jsonify({"files": files, "total": len(files)})

@app.route("/api/ipa", methods=["DELETE"])
@require_auth
def api_del_ipa():
    data = request.get_json(force=True) or {}
    filenames = data.get("filenames") or []
    if not isinstance(filenames, list) or not filenames:
        return jsonify({"error": "filenames[] 必填"}), 400
    deleted, errors = [], []
    cache_path = IPA_DIR.parent / ".scan_cache.json"
    cache = {}
    if cache_path.exists():
        try: cache = json.loads(cache_path.read_text())
        except: pass
    state = load_state()
    for fname in filenames:
        if "/" in fname or ".." in fname or not fname.endswith(".ipa"):
            errors.append(f"{fname}: 非法文件名")
            continue
        if fname in PROTECTED_FILES:
            errors.append(f"{fname}: 该文件受保护，禁止删除")
            continue
        ipa_path = IPA_DIR / fname
        if not ipa_path.exists():
            errors.append(f"{fname}: 文件不存在")
            continue
        try:
            ipa_path.unlink()
            icon_name = (cache.get(fname, {}).get("meta") or {}).get("icon_filename")
            if icon_name:
                icon_path = ICONS_DIR / icon_name
                if icon_path.exists():
                    icon_path.unlink()
            dv = state.get("downloaded_versions", {})
            for vk in list(dv.keys()):
                if vk and (vk in fname or fname.startswith(vk)):
                    del dv[vk]
            deleted.append(fname)
        except Exception as e:
            errors.append(f"{fname}: {e}")
    save_state(state)
    try:
        subprocess.run(["/usr/bin/python3", SCANNER_SCRIPT], timeout=60, capture_output=True)
    except Exception:
        pass
    return jsonify({"deleted": deleted, "errors": errors})

@app.route("/_ipa_proxy/<path:filename>", methods=["GET"])
@require_auth
def webui_download_ipa(filename):
    if "/" in filename or ".." in filename or not filename.endswith(".ipa"):
        abort(400)
    path = IPA_DIR / filename
    if not path.exists() or not path.is_file():
        abort(404)
    return send_from_directory(IPA_DIR, filename, as_attachment=True, download_name=filename)

# ============ API: 立即扫描 + 日志 ============
@app.route("/api/scan", methods=["POST"])
@require_auth
def api_scan():
    try:
        subprocess.Popen([SCAN_SCRIPT], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({"ok": True, "msg": "扫描已触发，看日志查看进度"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/reindex", methods=["POST"])
@require_auth
def api_reindex():
    """同步运行 scanner.py 重建 IPA 元信息缓存（不联网，纯本地解析）"""
    import time
    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "/app/scanner.py"],
            capture_output=True, timeout=180,
        )
        ok = proc.returncode == 0
        cache_path = IPA_DIR.parent / ".scan_cache.json"
        cache_count = 0
        if cache_path.exists():
            try:
                cache_count = len(json.loads(cache_path.read_text()))
            except Exception:
                pass
        return jsonify({
            "ok": ok,
            "elapsed_ms": int((time.time() - started) * 1000),
            "cache_count": cache_count,
            "stderr_tail": (proc.stderr or b"").decode("utf-8", "ignore")[-400:],
        })
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "scanner 超时（>180s）"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/logs/<name>", methods=["GET"])
@require_auth
def api_get_log(name):
    allow = {"tg-cron": "tg-cron.log", "scanner": "scanner.log",
             "nginx-error": "nginx-error.log"}
    if name not in allow:
        return jsonify({"error": "日志名非法"}), 400
    path = LOG_DIR / allow[name]
    if not path.exists():
        return jsonify({"lines": [], "msg": "日志为空"})
    lines = int(request.args.get("lines", "200"))
    lines = max(10, min(2000, lines))
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.readlines()
        return jsonify({"lines": content[-lines:], "total": len(content)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============ API: 解锁码（保留后端逻辑供 nginx/订阅用，只删前端 tab） ============
@app.route("/api/unlock", methods=["GET"])
@require_auth
def api_get_unlock():
    p = Path("/config/unlock.json")
    conf = {}
    if p.exists():
        try:
            conf = json.loads(p.read_text())
        except Exception:
            pass
    code = conf.get("code") or "142536"
    enabled = conf.get("enabled", True)
    token = str(conf.get("token") or "").strip()
    token_preview = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else "(未设置)"
    expires_at = conf.get("code_expires_at")  # ISO 字符串或 None；None / "" 表示永久
    now_ts = time.time()
    expired = False
    remaining_seconds = None
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at)
            remaining_seconds = int(exp_dt.timestamp() - now_ts)
            expired = remaining_seconds <= 0
        except Exception:
            expires_at = None
    return jsonify({
        "enabled": enabled,
        "code": code,
        "token_preview": token_preview,
        "has_token": bool(token),
        "expires_at": expires_at or "",
        "remaining_seconds": remaining_seconds,
        "expired": expired,
    })

@app.route("/api/unlock", methods=["DELETE"])
@require_auth
def api_regenerate_token():
    p = Path("/config/unlock.json")
    conf = {}
    if p.exists():
        try:
            conf = json.loads(p.read_text())
        except Exception:
            conf = {}
    new_token = secrets.token_urlsafe(24)
    conf["token"] = new_token
    p.write_text(json.dumps(conf, ensure_ascii=False, indent=2))
    return jsonify({"ok": True, "msg": "Token 已重新生成。需要重启容器生效，且所有订阅链接需要更新。", "token_preview": f"{new_token[:4]}...{new_token[-4:]}"})

@app.route("/api/unlock", methods=["PUT"])
@require_auth
def api_set_unlock():
    data = request.get_json(force=True) or {}
    enabled = bool(data.get("enabled", True))
    code = (data.get("code") or "").strip()
    if enabled:
        if not code or not code.isalnum() or len(code) < 4 or len(code) > 32:
            return jsonify({"error": "code 必须 4-32 位字母数字"}), 400
    # 有效期：accept "never" / "1d" / "7d" / "30d" / "90d" / ISO 字符串
    expires_raw = (data.get("expires") or "").strip()
    expires_at_iso = ""
    if expires_raw and expires_raw.lower() != "never":
        presets = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "365d": 365}
        if expires_raw in presets:
            exp_dt = datetime.now() + timedelta(days=presets[expires_raw])
            expires_at_iso = exp_dt.replace(microsecond=0).isoformat()
        else:
            try:
                exp_dt = datetime.fromisoformat(expires_raw)
                expires_at_iso = exp_dt.replace(microsecond=0).isoformat()
            except Exception:
                return jsonify({"error": "expires 必须为 never/1d/7d/30d/90d/365d 或 ISO 时间"}), 400
    p = Path("/config/unlock.json")
    old_conf = {}
    if p.exists():
        try:
            old_conf = json.loads(p.read_text())
        except Exception:
            old_conf = {}
    new_conf = dict(old_conf)
    new_conf.update({"enabled": enabled, "code": code, "code_expires_at": expires_at_iso})
    p.write_text(json.dumps(new_conf, ensure_ascii=False, indent=2))
    return jsonify({"ok": True, "msg": "已保存，立即生效。"})

@app.route("/auth", methods=["GET", "POST"])
def esign_unlock_auth():
    conf_path = Path("/config/unlock.json")
    conf = {}
    if conf_path.exists():
        try:
            conf = json.loads(conf_path.read_text())
        except Exception:
            conf = {}
    expected = str(conf.get("code") or "")
    got = (request.values.get("code") or request.values.get("password") or "").strip()
    udid = (request.values.get("udid") or request.values.get("UDID") or "").strip()
    lock_key = os.environ.get("IPA_LOCK_AUTH_KEY", "hoya_ipa_lock_v1")
    # 魔力签/全能签 README 规定的响应格式：仅 data + msg 两个字段，不带 code。
    # 成功: {"data": md5(key+udid), "msg": "解锁成功"}
    # 失败: {"data": md5(udid),     "msg": "<原因>"}
    udid_for_fail = udid or "unknown"
    fail_data = hashlib.md5(udid_for_fail.encode("utf-8")).hexdigest()
    # 有效期校验：过期立刻拒绝
    expires_at = conf.get("code_expires_at")
    if expires_at:
        try:
            if datetime.fromisoformat(expires_at).timestamp() <= time.time():
                return jsonify({"data": fail_data, "msg": "解锁码已过期，请联系源主获取新解锁码"})
        except Exception:
            pass
    if not udid:
        return jsonify({"data": fail_data, "msg": "缺少 UDID"})
    if not expected or got != expected:
        return jsonify({"data": fail_data, "msg": "解锁码错误"})
    digest = hashlib.md5((lock_key + udid).encode("utf-8")).hexdigest()
    return jsonify({"data": digest, "msg": "解锁成功"})

# ============ API: 修改 WebUI 密码 ============
def _valid_username(username: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9_.@-]{3,32}$", username))

def _hash_reset_code(username: str, code: str) -> str:
    raw = f"{username}:{code}:{_read_password()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def _load_reset_ticket():
    if not RESET_PATH.exists():
        return {}
    try:
        return json.loads(RESET_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_reset_ticket(ticket):
    RESET_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESET_PATH.write_text(json.dumps(ticket, ensure_ascii=False, indent=2), encoding="utf-8")

def _write_reset_log(username: str, code: str):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    RESET_LOG_PATH.write_text(
        f"[{ts}] WebUI password reset code for {username}: {code} (expires in 10 minutes)\n",
        encoding="utf-8",
    )

def _send_password_reset_code(username: str, code: str):
    text = (
        "🌸 WebUI 密码重置验证码来啦～\n"
        f"👤 账号：{username}\n"
        f"🔐 验证码：{code}\n"
        "⏰ 10 分钟内有效喔。\n\n"
        "如果不是你本人操作，请忽略这条消息就好啦 ✨"
    )
    return _send_bot_message(text)

@app.route("/api/account", methods=["GET"])
@require_auth
def api_get_account():
    return jsonify({"username": _read_username()})

@app.route("/api/account", methods=["PUT"])
@require_auth
def api_set_account():
    data = request.get_json(force=True) or {}
    next_user = (data.get("username") or _read_username()).strip()
    next_pw = (data.get("new_password") or "").strip()

    if not _valid_username(next_user):
        return jsonify({"error": "用户名必须 3-32 位，只能包含字母、数字、点、下划线、@ 或 -" }), 400
    if next_pw and (len(next_pw) < 4 or len(next_pw) > 64):
        return jsonify({"error": "新密码必须 4-64 位"}), 400
    if next_user == _read_username() and not next_pw:
        return jsonify({"ok": True, "msg": "没有改动"})

    _write_auth(username=next_user, password=(next_pw or None))
    _clear_sessions()
    return jsonify({"ok": True, "msg": "账号已更新，请重新登录。"})

@app.route("/api/password", methods=["PUT"])
@require_auth
def api_set_password():
    data = request.get_json(force=True) or {}
    new_pw = (data.get("new_password") or "").strip()
    if len(new_pw) < 4 or len(new_pw) > 64:
        return jsonify({"error": "新密码必须 4-64 位"}), 400
    if new_pw == _read_password():
        return jsonify({"error": "新密码不能与当前密码相同"}), 400
    _write_password(new_pw)
    # 改密码后清所有 session，强制重新登录
    _clear_sessions()
    return jsonify({"ok": True, "msg": "密码已修改，请重新登录。"})

@app.route("/api/password/forgot/request", methods=["POST"])
def api_forgot_password_request():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    if username != _read_username():
        return jsonify({"error": "用户名不存在"}), 404

    old_ticket = _load_reset_ticket()
    if old_ticket.get("username") == username and time.time() - float(old_ticket.get("issued_at", 0)) < 60:
        return jsonify({"error": "验证码刚刚发过，请稍等 1 分钟再试"}), 429

    code = f"{secrets.randbelow(1000000):06d}"
    ticket = {
        "username": username,
        "code_hash": _hash_reset_code(username, code),
        "issued_at": time.time(),
        "expires_at": time.time() + RESET_CODE_TTL,
        "attempts": 0,
    }
    _save_reset_ticket(ticket)

    sent_count = 0
    try:
        sent_count = _send_password_reset_code(username, code)
    except Exception as e:
        app.logger.warning("发送密码重置验证码失败: %s", e)

    if not sent_count:
        _write_reset_log(username, code)
        return jsonify({
            "ok": True,
            "delivery": "log",
            "msg": "Telegram 暂时没发出去，验证码已写入 /logs/webui-reset.log",
        })
    return jsonify({
        "ok": True,
        "delivery": "telegram",
        "msg": "验证码已发送到 Telegram，请 10 分钟内使用。",
    })

@app.route("/api/password/forgot/confirm", methods=["POST"])
def api_forgot_password_confirm():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    code = (data.get("code") or "").strip()
    new_pw = (data.get("new_password") or "").strip()
    ticket = _load_reset_ticket()

    if len(new_pw) < 4 or len(new_pw) > 64:
        return jsonify({"error": "新密码必须 4-64 位"}), 400
    if new_pw == _read_password():
        return jsonify({"error": "新密码不能与当前密码相同"}), 400
    if not ticket or ticket.get("username") != username:
        return jsonify({"error": "请先获取验证码"}), 400
    if float(ticket.get("expires_at", 0)) < time.time():
        try:
            RESET_PATH.unlink()
        except OSError:
            pass
        return jsonify({"error": "验证码已过期，请重新获取"}), 400
    if int(ticket.get("attempts", 0)) >= 8:
        return jsonify({"error": "尝试次数过多，请重新获取验证码"}), 429

    expected = str(ticket.get("code_hash") or "")
    got = _hash_reset_code(username, code)
    if not secrets.compare_digest(expected, got):
        ticket["attempts"] = int(ticket.get("attempts", 0)) + 1
        _save_reset_ticket(ticket)
        return jsonify({"error": "验证码不正确"}), 400

    _write_password(new_pw)
    _clear_sessions()
    try:
        RESET_PATH.unlink()
    except OSError:
        pass
    return jsonify({"ok": True, "msg": "密码已重置，请重新登录。"})



# ============ API: 网络代理 ============
PROXY_PATH = Path("/config/proxy.json")

# 预设代理列表（可通过环境变量 PROXY_PRESETS 注入 JSON 数组覆盖）
def _default_proxy_presets():
    raw = os.environ.get("PROXY_PRESETS", "").strip()
    if raw:
        try:
            items = json.loads(raw)
            if isinstance(items, list):
                return items
        except Exception:
            pass
    return []

PROXY_PRESETS = _default_proxy_presets()

def _load_proxy_config():
    """加载代理配置"""
    if PROXY_PATH.exists():
        try:
            return json.loads(PROXY_PATH.read_text())
        except Exception:
            pass
    return {"url": "", "enabled": True, "source": ""}

def _save_proxy_config(cfg):
    """保存代理配置"""
    PROXY_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROXY_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))

@app.route("/api/proxy", methods=["GET"])
@require_auth
def api_get_proxy():
    """获取代理配置 + 预设列表"""
    cfg = _load_proxy_config()
    # 脱敏：不暴露完整 URL 中的密码部分（如果有的话）
    safe_url = cfg.get("url", "")
    return jsonify({
        "url": safe_url,
        "enabled": cfg.get("enabled", True),
        "source": cfg.get("source", ""),
        "presets": PROXY_PRESETS,
        "env_tg_proxy": os.environ.get("TG_PROXY", ""),
        "env_forward_proxy": os.environ.get("FORWARD_BOT_PROXY", ""),
    })

@app.route("/api/proxy", methods=["PUT"])
@require_auth
def api_set_proxy():
    """保存代理配置"""
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    enabled = bool(data.get("enabled", True))
    source = (data.get("source") or "").strip()  # "preset" or "custom"

    if url:
        # 基本校验格式
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.hostname:
            return jsonify({"error": "代理格式错误，示例: socks5://127.0.0.1:1080"}), 400
        if parsed.scheme.lower() not in ("socks5", "socks5h", "http", "https"):
            return jsonify({"error": "仅支持 socks5/http/https 协议"}), 400

    cfg = {"url": url, "enabled": enabled, "source": source}
    _save_proxy_config(cfg)

    return jsonify({"ok": True, "msg": "代理已保存。Bot 验证、TG 登录和后续扫描都会使用这份代理配置。"})

@app.route("/api/proxy/test", methods=["POST"])
@require_auth
def api_test_proxy():
    """测试代理是否可用：通过代理访问 Telegram API"""
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()

    if not url:
        # 如果没给 URL，用当前保存的配置
        cfg = _load_proxy_config()
        url = cfg.get("url", "")
        if not url:
            return jsonify({"ok": False, "error": "未设置代理地址"}), 400

    # 校验格式
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        return jsonify({"ok": False, "error": "代理格式错误"}), 400

    try:
        import httpx
        # 测试1: 通过代理访问 Telegram API（验证代理是否能连 TG）
        start = time.time()
        try:
            with httpx.Client(proxy=url, timeout=10) as c:
                r = c.get("https://api.telegram.org/")
                tg_time = round((time.time() - start) * 1000)
                tg_ok = r.status_code < 500
        except Exception as e:
            tg_time = None
            tg_ok = False
            tg_err = str(e)[:100]

        # 测试2: 通过代理访问 Google（验证代理是否真正出墙）
        start2 = time.time()
        try:
            with httpx.Client(proxy=url, timeout=10) as c:
                r2 = c.get("https://www.google.com/generate_204")
                gw_time = round((time.time() - start2) * 1000)
                gw_ok = r2.status_code == 204
        except Exception:
            gw_time = None
            gw_ok = False

        # 测试3: 直连 Telegram（不需要代理也能访问？）
        start3 = time.time()
        try:
            with httpx.Client(timeout=10) as c:
                r3 = c.get("https://api.telegram.org/")
                direct_time = round((time.time() - start3) * 1000)
                direct_ok = r3.status_code < 500
        except Exception:
            direct_time = None
            direct_ok = False

        result = {
            "ok": tg_ok,
            "proxy_url": url,
            "tests": {
                "telegram_via_proxy": {"ok": tg_ok, "latency_ms": tg_time} if tg_time else {"ok": False, "error": tg_err},
                "google_via_proxy": {"ok": gw_ok, "latency_ms": gw_time} if gw_time else {"ok": False},
                "telegram_direct": {"ok": direct_ok, "latency_ms": direct_time} if direct_time else {"ok": False},
            }
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


def _active_proxy_url():
    cfg = _load_proxy_config()
    url = str(cfg.get("url") or "").strip()
    if cfg.get("enabled", True) and url:
        return url
    return os.environ.get("TG_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""

BOT_COMMANDS = [
    {"command": "start", "description": "打开 IPA 小助手"},
    {"command": "status", "description": "查看仓库状态"},
    {"command": "apps", "description": "查看白名单 APP"},
    {"command": "scan", "description": "触发后台扫描"},
    {"command": "help", "description": "使用说明"},
]

def _forward_bot_config():
    if not FORWARD_BOT_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(FORWARD_BOT_CONFIG_PATH.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}

def _telegram_get_me(token: str):
    import httpx
    proxy_url = _active_proxy_url()
    with httpx.Client(proxy=proxy_url or None, timeout=12) as c:
        return c.get(f"https://api.telegram.org/bot{token}/getMe"), proxy_url

def _telegram_post(token: str, method: str, payload: dict, timeout=15):
    import httpx
    proxy_url = _active_proxy_url()
    with httpx.Client(proxy=proxy_url or None, timeout=timeout) as c:
        return c.post(f"https://api.telegram.org/bot{token}/{method}", json=payload), proxy_url

def _set_bot_commands(token: str) -> bool:
    if not token:
        return False
    try:
        r, _proxy_url = _telegram_post(token, "setMyCommands", {"commands": BOT_COMMANDS})
        return r.status_code == 200 and r.json().get("ok")
    except Exception as e:
        app.logger.warning("设置 Bot 菜单失败: %s", e)
        return False

def _normalize_user_ids(value):
    if value is None:
        return []
    if isinstance(value, str):
        raw = re.split(r"[\s,，;；]+", value.strip())
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = [value]

    user_ids = []
    seen = set()
    for item in raw:
        s = str(item).strip()
        if not s:
            continue
        if not re.fullmatch(r"\d{1,20}", s):
            raise ValueError(f"USER_ID 无效: {s}")
        if s not in seen:
            seen.add(s)
            user_ids.append(s)
    return user_ids

def _forward_bot_user_ids():
    data = _forward_bot_config()
    ids = []
    ids.extend(_normalize_user_ids(data.get("user_ids", [])))
    ids.extend(_normalize_user_ids(data.get("user_id")) if data.get("user_id") else [])
    return _normalize_user_ids(ids)

def _sync_forward_bot_user_ids(user_ids):
    if not FORWARD_BOT_CONFIG_PATH.exists():
        return
    try:
        data = json.loads(FORWARD_BOT_CONFIG_PATH.read_text(encoding="utf-8") or "{}")
        if user_ids:
            data["user_id"] = user_ids[0]
            data["user_ids"] = user_ids
        else:
            data.pop("user_id", None)
            data.pop("user_ids", None)
        FORWARD_BOT_CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        app.logger.warning("同步 forward_bot USER_ID 失败: %s", e)

def _sync_forward_bot_meta(token=None, bot_username=None, user_ids=None):
    if not FORWARD_BOT_CONFIG_PATH.exists():
        return
    try:
        data = _forward_bot_config()
        if token:
            data["bot_token"] = token
        if bot_username:
            data["bot_username"] = bot_username
        if user_ids is not None:
            if user_ids:
                data["user_id"] = user_ids[0]
                data["user_ids"] = user_ids
            else:
                data.pop("user_id", None)
                data.pop("user_ids", None)
        FORWARD_BOT_CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        app.logger.warning("同步 forward_bot 配置失败: %s", e)

def _notification_bot_config(cfg=None):
    cfg = cfg or load_config()
    notify = cfg.get("telegram_notify", {}) or {}
    forward = _forward_bot_config()
    token = str(notify.get("bot_token") or forward.get("bot_token") or "").strip()
    user_ids = []
    user_ids.extend(_normalize_user_ids(notify.get("chat_id")))
    user_ids.extend(_normalize_user_ids(notify.get("chat_ids")))
    user_ids.extend(_normalize_user_ids(notify.get("user_ids")))
    user_ids.extend(_normalize_user_ids(notify.get("user_id")))
    user_ids.extend(_normalize_user_ids(forward.get("user_ids")))
    user_ids.extend(_normalize_user_ids(forward.get("user_id")))
    return {
        "enabled": bool(token) and notify.get("enabled", True) is not False,
        "token": token,
        "bot_username": notify.get("bot_username") or forward.get("bot_username") or "",
        "user_ids": _normalize_user_ids(user_ids),
    }

def _send_bot_message(text: str) -> int:
    bot = _notification_bot_config()
    if not bot["enabled"] or not bot["user_ids"]:
        return 0
    sent = 0
    for chat_id in bot["user_ids"]:
        try:
            r, _proxy_url = _telegram_post(bot["token"], "sendMessage", {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            })
            if r.status_code == 200 and r.json().get("ok"):
                sent += 1
        except Exception as e:
            app.logger.warning("Bot 消息发送失败 %s: %s", chat_id, e)
    return sent

# ============ API: Telegram Bot 配置 ============
@app.route("/api/bot", methods=["GET"])
@require_auth
def api_get_bot():
    """获取 Bot 配置（token 脱敏）"""
    cfg = load_config()
    notify = cfg.get("telegram_notify", {})
    forward = _forward_bot_config()
    token = str(notify.get("bot_token") or forward.get("bot_token") or "").strip()
    token_preview = ""
    if token:
        token_preview = f"{token[:6]}...{token[-4:]}" if len(token) > 12 else "***"
    user_ids = []
    try:
        user_ids = _normalize_user_ids(notify.get("user_ids") or notify.get("user_id") or [])
    except ValueError:
        user_ids = []
    if not user_ids:
        user_ids = _forward_bot_user_ids()
    return jsonify({
        "enabled": notify.get("enabled", bool(token)),
        "token_preview": token_preview,
        "token": token,
        "has_token": bool(token),
        "chat_id": notify.get("chat_id"),
        "bot_username": notify.get("bot_username") or forward.get("bot_username", ""),
        "user_ids": user_ids,
    })

@app.route("/api/bot", methods=["PUT"])
@require_auth
def api_set_bot():
    """保存 Bot Token"""
    data = request.get_json(force=True) or {}
    token = (data.get("bot_token") or "").strip()
    chat_id = data.get("chat_id")
    enabled = bool(data.get("enabled", True))
    try:
        user_ids = _normalize_user_ids(data.get("user_ids")) if "user_ids" in data else None
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    cfg = load_config()
    notify = cfg.setdefault("telegram_notify", {})
    forward = _forward_bot_config()
    old_token = str(notify.get("bot_token") or forward.get("bot_token") or "").strip()
    if enabled and not token and not old_token:
        return jsonify({"error": "Bot Token 不能为空"}), 400

    # 如果给了新 token，先验证再保存
    if token and token != old_token:
        try:
            r, proxy_url = _telegram_get_me(token)
            if r.status_code == 200 and r.json().get("ok"):
                notify["bot_username"] = r.json()["result"]["username"]
                notify["bot_token"] = token
                notify["enabled"] = enabled
                if chat_id is not None:
                    notify["chat_id"] = chat_id
                if user_ids is not None:
                    notify["user_ids"] = user_ids
                _sync_forward_bot_meta(token=token, bot_username=notify["bot_username"], user_ids=user_ids)
                _set_bot_commands(token)
                save_config(cfg)
                return jsonify({"ok": True, "msg": "Bot 已保存，菜单已同步"})
            else:
                return jsonify({"error": f"Token 无效: {r.json().get('description', r.text)[:100]}"}), 400
        except Exception as e:
            proxy_hint = "，已使用代理" if _active_proxy_url() else "，未配置代理"
            return jsonify({"error": f"验证失败{proxy_hint}: {str(e)[:120]}"}), 500
    else:
        # 不修改 token，只更新其他字段
        notify["enabled"] = enabled
        if chat_id is not None:
            notify["chat_id"] = chat_id
        if user_ids is not None:
            notify["user_ids"] = user_ids
            _sync_forward_bot_user_ids(user_ids)
        if old_token:
            _set_bot_commands(old_token)
        save_config(cfg)
        return jsonify({"ok": True, "msg": "已保存，Bot 菜单已同步"})

@app.route("/api/bot/test", methods=["POST"])
@require_auth
def api_test_bot():
    """测试已保存的 Bot Token"""
    cfg = load_config()
    token = str((cfg.get("telegram_notify") or {}).get("bot_token") or _forward_bot_config().get("bot_token") or "").strip()
    if not token:
        return jsonify({"ok": False, "error": "未配置 Bot Token"}), 400
    try:
        r, proxy_url = _telegram_get_me(token)
        if r.status_code == 200 and r.json().get("ok"):
            _set_bot_commands(token)
            info = r.json()["result"]
            return jsonify({"ok": True, "proxy": bool(proxy_url), "bot": {"username": info["username"], "name": info["first_name"], "id": info["id"]}})
        return jsonify({"ok": False, "error": f"Token 无效: {r.json().get('description', r.text)[:100]}"})
    except Exception as e:
        proxy_hint = "已使用代理" if _active_proxy_url() else "未配置代理"
        return jsonify({"ok": False, "error": f"{proxy_hint}: {str(e)[:120]}"}), 500


# ============ API: 状态 + 重启 ============
@app.route("/api/status", methods=["GET"])
@require_auth
def api_status():
    state = load_state()
    ipa_count = len(list(IPA_DIR.glob("*.ipa"))) if IPA_DIR.exists() else 0
    return jsonify({
        "ipa_count": ipa_count,
        "last_scan": state.get("last_scan", "未知"),
        "downloaded_versions_count": len(state.get("downloaded_versions", {})),
    })

@app.route("/api/restart", methods=["POST"])
@require_auth
def api_restart():
    def _exit():
        time.sleep(1)
        os.kill(1, 15)
    import threading
    threading.Thread(target=_exit, daemon=True).start()
    return jsonify({"ok": True, "msg": "容器将在 1 秒后重启，请稍候 5-10 秒刷新页面"})

# ============ 静态文件 + 首页 ============
@app.route("/")
@require_auth
def index():
    return send_from_directory(STATIC_DIR, "index.html")

@app.route("/static/<path:p>")
@require_auth
def static_files(p):
    if p.startswith("icons/"):
        return send_from_directory(ICONS_DIR, p.removeprefix("icons/"))
    return send_from_directory(STATIC_DIR, p)

@app.route("/icons/<path:p>")
@require_auth
def webui_icon_files(p):
    """WebUI 使用的原生 App 图标代理，文件来自 /data/icons。"""
    return send_from_directory(ICONS_DIR, p)

@app.route("/static/icons/<path:p>")
@require_auth
def icon_files(p):
    """App 图标存放在 /data/icons/ 卷挂载目录"""
    return send_from_directory(ICONS_DIR, p)

if __name__ == "__main__":
    port = int(os.environ.get("WEBUI_PORT", "8085"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
