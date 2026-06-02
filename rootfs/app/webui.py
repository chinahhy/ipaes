#!/usr/bin/env python3
# IPA Self-Host WebUI backend (Flask)
# Config CRUD + IPA file mgmt + scan trigger + log streaming
import os, json, shutil, subprocess, time, base64
from datetime import datetime
from pathlib import Path
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, Response, abort

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
# 这样进 UI 改完密码会持久化，env 只作为初始值
PASS_PATH = Path("/config/webui_pass.json")
_ENV_PASS = os.environ.get("WEBUI" + "_" + "PASS" + "WORD", "admin")

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


app = Flask(__name__, static_folder=None)

# ============ 工具函数 ============
def load_config():
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

def save_config(cfg):
    # 备份原文件
    if CONFIG_PATH.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = CONFIG_PATH.with_suffix(f".json.bak-{ts}")
        shutil.copy2(CONFIG_PATH, bak)
        # 只保留最近 10 个备份
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
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )

def repo_path_from_url(url):
    """从 REPO_BASE_URL 提取末段路径（解锁码）"""
    u = (url or "").rstrip("/")
    if not u:
        return "repo"
    parts = [p for p in u.split("/") if p and "//" not in p]
    return parts[-1] if parts else "repo"

# ============ Basic Auth ============
def check_auth():
    a = request.authorization
    return a and a.username == UI_USER and a.password == _read_password()

def require_auth(f):
    @wraps(f)
    def wrapper(*a, **k):
        if not check_auth():
            return Response(
                "Auth required", 401,
                {"WWW-Authenticate": 'Basic realm="ipa-self-host"'}
            )
        return f(*a, **k)
    return wrapper

# ============ API: 配置 ============
@app.route("/api/config", methods=["GET"])
@require_auth
def api_get_config():
    cfg = load_config()
    # 脱敏：不返回 api_id/api_hash/phone
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
    """新增 App 或给已有 App 加关键词"""
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
        # 合并去重
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
    """整体替换某 App 的关键词列表"""
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
    """列出所有 IPA 文件。从 scanner 缓存 + 文件系统读取元信息"""
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
    """删 IPA + 对应 icon + 从 state 移除版本记录"""
    data = request.get_json(force=True) or {}
    filenames = data.get("filenames") or []
    if not isinstance(filenames, list) or not filenames:
        return jsonify({"error": "filenames[] 必填"}), 400
    deleted, errors = [], []
    # 加载 scan_cache 找到对应 icon
    cache_path = Path("/data/.scan_cache.json")
    cache = {}
    if cache_path.exists():
        try: cache = json.loads(cache_path.read_text())
        except: pass
    state = load_state()
    for fname in filenames:
        # 安全检查：不允许路径穿越
        if "/" in fname or ".." in fname or not fname.endswith(".ipa"):
            errors.append(f"{fname}: 非法文件名")
            continue
        ipa_path = IPA_DIR / fname
        if not ipa_path.exists():
            errors.append(f"{fname}: 文件不存在")
            continue
        try:
            ipa_path.unlink()
            # 删 icon
            icon_name = (cache.get(fname, {}).get("meta") or {}).get("icon_filename")
            if icon_name:
                icon_path = ICONS_DIR / icon_name
                if icon_path.exists():
                    icon_path.unlink()
            # 从 state 的 downloaded_versions 清掉对应 version_key（让它能重新下）
            dv = state.get("downloaded_versions", {})
            # version_key 形如 "AppName_1.2.3"，只能模糊匹配；保守做法是删能在文件名找到的
            for vk in list(dv.keys()):
                if vk and (vk in fname or fname.startswith(vk)):
                    del dv[vk]
            deleted.append(fname)
        except Exception as e:
            errors.append(f"{fname}: {e}")
    save_state(state)
    # 触发 scanner 重建 repo.json
    try:
        subprocess.run(["/usr/bin/python3", SCANNER_SCRIPT], timeout=60, capture_output=True)
    except Exception:
        pass
    return jsonify({"deleted": deleted, "errors": errors})

# ============ API: 立即扫描 + 日志 ============
@app.route("/api/scan", methods=["POST"])
@require_auth
def api_scan():
    """非阻塞触发一次 TG 扫描"""
    try:
        subprocess.Popen([SCAN_SCRIPT], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({"ok": True, "msg": "扫描已触发，看日志查看进度"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/logs/<name>", methods=["GET"])
@require_auth
def api_get_log(name):
    """读取日志最后 N 行。name ∈ {tg-cron, scanner, nginx-error}"""
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

# ============ API: 解锁码 ============
@app.route("/api/unlock", methods=["GET"])
@require_auth
def api_get_unlock():
    """读取当前解锁码状态（从 /config/unlock.json）"""
    p = Path("/config/unlock.json")
    if p.exists():
        try:
            return jsonify(json.loads(p.read_text()))
        except Exception:
            pass
    # 默认值
    return jsonify({"enabled": True, "code": "142536"})

@app.route("/api/unlock", methods=["PUT"])
@require_auth
def api_set_unlock():
    """修改解锁码 / 开关。改完需要重启容器才能让 nginx 生效。"""
    data = request.get_json(force=True) or {}
    enabled = bool(data.get("enabled", True))
    code = (data.get("code") or "").strip()
    if enabled:
        if not code or not code.isalnum() or len(code) < 4 or len(code) > 32:
            return jsonify({"error": "code 必须 4-32 位字母数字"}), 400
    p = Path("/config/unlock.json")
    p.write_text(json.dumps({"enabled": enabled, "code": code}, ensure_ascii=False, indent=2))
    return jsonify({"ok": True, "msg": "已保存。需要重启容器生效（点右上 ↻ 重启按钮）"})

# ============ API: 修改 WebUI 密码 ============
@app.route("/api/password", methods=["PUT"])
@require_auth
def api_set_password():
    """改 WebUI 登录密码。必须先用旧密码通过 Basic Auth，再 body 给新密码"""
    data = request.get_json(force=True) or {}
    old_pw = (data.get("old_password") or "").strip()
    new_pw = (data.get("new_password") or "").strip()
    # 二次校验旧密码（Basic Auth 已过，但避免别人趁登录会话改密码）
    if old_pw != _read_password():
        return jsonify({"error": "旧密码不正确"}), 403
    # 校验新密码强度
    if len(new_pw) < 4 or len(new_pw) > 64:
        return jsonify({"error": "新密码必须 4-64 位"}), 400
    if new_pw == old_pw:
        return jsonify({"error": "新密码不能与旧密码相同"}), 400
    _write_password(new_pw)
    return jsonify({"ok": True, "msg": "密码已修改。下次访问需用新密码登录。"})

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
    """触发容器自身退出，让 docker --restart=unless-stopped 拉起"""
    def _exit():
        time.sleep(1)
        os.kill(1, 15)  # SIGTERM to PID 1
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

if __name__ == "__main__":
    port = int(os.environ.get("WEBUI_PORT", "8085"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
