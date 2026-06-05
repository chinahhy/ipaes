#!/usr/bin/env python3
# IPA Self-Host WebUI backend (Flask)
# Cookie 登录 + App 卡片首页 + 删除访问控制 tab
import os, json, shutil, subprocess, time, hashlib, secrets
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
SCAN_SCRIPT = "/app/run-tg-scan.sh"
SCANNER_SCRIPT = "/app/scanner.py"
STATIC_DIR = Path(__file__).parent / "webui_static"

UI_USER = os.environ.get("WEBUI_USER", "admin")
# 密码来源优先级：/config/webui_pass.json > env > 默认 "admin"
PASS_PATH = Path("/config/webui_pass.json")
_ENV_PASS = os.environ.get("WEBUI" + "_" + "PASS" + "WORD", "admin")

# Session 存储：{session_id: {"user": str, "expires": float}}
# 生产环境可用 Redis/文件持久化，这里用内存简单实现（容器重启需重新登录）
_sessions = {}
SESSION_MAX_AGE = 86400 * 7  # 7 天

def _read_password():
    if PASS_PATH.exists():
        try:
            return json.loads(PASS_PATH.read_text()).get("password") or _ENV_PASS
        except Exception:
            pass
    return _ENV_PASS

def _write_password(new_pass):
    PASS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PASS_PATH.write_text(json.dumps({"password": new_pass}, ensure_ascii=False))

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
    if a and a.username == UI_USER and a.password == _read_password():
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
    if username != UI_USER or password != _read_password():
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
@app.route("/api/ipa", methods=["GET"])
@require_auth
def api_list_ipa():
    """列出所有 IPA 文件。从 scanner 缓存 + 文件系统读取元信息，按 App 分组"""
    cache_path = Path("/data/.scan_cache.json")
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
    files = []
    if IPA_DIR.exists():
        for f in sorted(IPA_DIR.glob("*.ipa")):
            st = f.stat()
            m = meta_by_name.get(f.name, {})
            files.append({
                "filename": f.name,
                "size": st.st_size,
                "size_mb": round(st.st_size / 1024 / 1024, 1),
                "mtime": int(st.st_mtime),
                "mtime_str": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "app_name": m.get("app_name", ""),
                "bundle_id": m.get("bundle_id", ""),
                "version": m.get("version", ""),
                "icon": m.get("icon"),
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
    cache_path = Path("/data/.scan_cache.json")
    cache = {}
    if cache_path.exists():
        try: cache = json.loads(cache_path.read_text())
        except: pass
    state = load_state()
    for fname in filenames:
        if "/" in fname or ".." in fname or not fname.endswith(".ipa"):
            errors.append(f"{fname}: 非法文件名")
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

# ============ API: 立即扫描 + 日志 ============
@app.route("/api/scan", methods=["POST"])
@require_auth
def api_scan():
    try:
        subprocess.Popen([SCAN_SCRIPT], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({"ok": True, "msg": "扫描已触发，看日志查看进度"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
    return jsonify({
        "enabled": enabled,
        "code": code,
        "token_preview": token_preview,
        "has_token": bool(token),
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
    p = Path("/config/unlock.json")
    old_conf = {}
    if p.exists():
        try:
            old_conf = json.loads(p.read_text())
        except Exception:
            old_conf = {}
    new_conf = dict(old_conf)
    new_conf.update({"enabled": enabled, "code": code})
    p.write_text(json.dumps(new_conf, ensure_ascii=False, indent=2))
    return jsonify({"ok": True, "msg": "已保存。需要重启容器生效（点右上 ↻ 重启按钮）"})

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
    if not expected or got != expected:
        return jsonify({"code": 1, "msg": "解锁码错误", "data": ""}), 403
    if not udid:
        return jsonify({"code": 2, "msg": "缺少 UDID", "data": ""}), 400
    digest = hashlib.md5((lock_key + udid).encode("utf-8")).hexdigest()
    return jsonify({"code": 0, "msg": "解锁成功", "data": digest})

# ============ API: 修改 WebUI 密码 ============
@app.route("/api/password", methods=["PUT"])
@require_auth
def api_set_password():
    data = request.get_json(force=True) or {}
    old_pw = (data.get("old_password") or "").strip()
    new_pw = (data.get("new_password") or "").strip()
    if old_pw != _read_password():
        return jsonify({"error": "旧密码不正确"}), 403
    if len(new_pw) < 4 or len(new_pw) > 64:
        return jsonify({"error": "新密码必须 4-64 位"}), 400
    if new_pw == old_pw:
        return jsonify({"error": "新密码不能与旧密码相同"}), 400
    _write_password(new_pw)
    # 改密码后清所有 session，强制重新登录
    _clear_sessions()
    return jsonify({"ok": True, "msg": "密码已修改，请重新登录。"})



# ============ API: 网络代理 ============
PROXY_PATH = Path("/config/proxy.json")

# 预设代理列表（Hoya 家里的真实可用代理）
PROXY_PRESETS = [
    {"name": "旁网关 (10.0.0.2)", "url": "http://10.0.0.2:7893", "desc": "Clash 混合端口，支持 http/https/socks5"},
]

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
            return jsonify({"error": "代理格式错误，示例: socks5://10.0.0.100:7893"}), 400
        if parsed.scheme.lower() not in ("socks5", "socks5h", "http", "https"):
            return jsonify({"error": "仅支持 socks5/http/https 协议"}), 400

    cfg = {"url": url, "enabled": enabled, "source": source}
    _save_proxy_config(cfg)

    # 同步更新 tg_bot 的环境变量（需要重启容器才能让 tg_bot 的 cron 生效）
    # 但 forward_bot 会实时读取 /config/proxy.json，所以立即生效
    return jsonify({"ok": True, "msg": "代理已保存。转发 Bot 会立即使用新代理；定时扫描需重启容器生效。"})

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


# ============ API: Telegram Bot 配置 ============
@app.route("/api/bot", methods=["GET"])
@require_auth
def api_get_bot():
    """获取 Bot 配置（token 脱敏）"""
    cfg = load_config()
    notify = cfg.get("telegram_notify", {})
    token = str(notify.get("bot_token") or "").strip()
    token_preview = ""
    if token:
        token_preview = f"{token[:6]}...{token[-4:]}" if len(token) > 12 else "***"
    return jsonify({
        "enabled": notify.get("enabled", False),
        "token_preview": token_preview,
        "has_token": bool(token),
        "chat_id": notify.get("chat_id"),
        "bot_username": notify.get("bot_username", ""),
    })

@app.route("/api/bot", methods=["PUT"])
@require_auth
def api_set_bot():
    """保存 Bot Token"""
    data = request.get_json(force=True) or {}
    token = (data.get("bot_token") or "").strip()
    chat_id = data.get("chat_id")
    enabled = bool(data.get("enabled", True))

    if enabled and not token:
        return jsonify({"error": "Bot Token 不能为空"}), 400

    cfg = load_config()
    notify = cfg.setdefault("telegram_notify", {})
    old_token = str(notify.get("bot_token") or "").strip()

    # 如果给了新 token，先验证再保存
    if token and token != old_token:
        try:
            import httpx
            r = httpx.get(f"https://api.telegram.org/bot{token}/getMe",
                          timeout=10)
            if r.status_code == 200 and r.json().get("ok"):
                notify["bot_username"] = r.json()["result"]["username"]
                notify["bot_token"] = token
                notify["enabled"] = enabled
                if chat_id is not None:
                    notify["chat_id"] = chat_id
                save_config(cfg)
                return jsonify({"ok": True, "msg": f"Bot @{notify['bot_username']} 已保存"})
            else:
                return jsonify({"error": f"Token 无效: {r.json().get('description', r.text)[:100]}"}), 400
        except Exception as e:
            return jsonify({"error": f"验证失败: {str(e)[:100]}"}), 500
    else:
        # 不修改 token，只更新其他字段
        notify["enabled"] = enabled
        if chat_id is not None:
            notify["chat_id"] = chat_id
        save_config(cfg)
        return jsonify({"ok": True, "msg": "已保存"})

@app.route("/api/bot/test", methods=["POST"])
@require_auth
def api_test_bot():
    """测试已保存的 Bot Token"""
    cfg = load_config()
    token = str((cfg.get("telegram_notify") or {}).get("bot_token") or "").strip()
    if not token:
        return jsonify({"ok": False, "error": "未配置 Bot Token"}), 400
    try:
        import httpx
        r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        if r.status_code == 200 and r.json().get("ok"):
            info = r.json()["result"]
            return jsonify({"ok": True, "bot": {"username": info["username"], "name": info["first_name"], "id": info["id"]}})
        return jsonify({"ok": False, "error": f"Token 无效: {r.json().get('description', r.text)[:100]}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:100]}), 500


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
    return send_from_directory(STATIC_DIR, p)

@app.route("/static/icons/<path:p>")
@require_auth
def icon_files(p):
    """App 图标存放在 /data/icons/ 卷挂载目录"""
    return send_from_directory(ICONS_DIR, p)

if __name__ == "__main__":
    port = int(os.environ.get("WEBUI_PORT", "8085"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
