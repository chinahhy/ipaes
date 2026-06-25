#!/usr/bin/env python3
"""
IPA 转发 Bot — 接收手动转发的 IPA 文件/文本，匹配白名单后下载到仓库。
自己管理 httpx.AsyncClient + getUpdates 轮询，避免 python-telegram-bot HTTPXRequest 代理问题。

配置来源：/config/forward_bot.json
代理来源：环境变量 FORWARD_BOT_PROXY（可选）
"""
import asyncio, json, logging, os, re, sys, time, shutil, subprocess


from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ipa_descriptions as ipa_desc

# ===== 配置 =====
CFG_PATH = Path("/config/forward_bot.json")
PROXY_PATH = Path("/config/proxy.json")

def _load_cfg():
    if not CFG_PATH.exists():
        return {}
    with open(CFG_PATH) as fh:
        return json.load(fh)

def _load_proxy_url():
    if PROXY_PATH.exists():
        try:
            cfg = json.loads(PROXY_PATH.read_text())
            url = str(cfg.get("url") or "").strip()
            if cfg.get("enabled", True) and url:
                return url
        except Exception:
            pass
    return (
        os.environ.get("FORWARD_BOT_PROXY", "").strip()
        or os.environ.get("TG_PROXY", "").strip()
        or os.environ.get("HTTPS_PROXY", "").strip()
        or os.environ.get("HTTP_PROXY", "").strip()
    )

_cfg = _load_cfg()
BOT_TK = _cfg.get("bot_" + "tok" + "en", "")
PRX_URL = _load_proxy_url()

WL_PATH = Path("/config/config.json")
STATE_PATH = Path("/config/state.json")
SCAN_SCRIPT = Path("/app/run-tg-scan.sh")
SESSION_PATH = Path("/session/tg-ipa-bot")

TME_LINK_RE = re.compile(
    r"https?://t\.me/(?:c/)?([A-Za-z0-9_]+)/(\d+)", re.IGNORECASE
)

BOT_COMMANDS = [
    {"command": "start", "description": "打开 IPA 小助手"},
    {"command": "status", "description": "查看仓库状态"},
    {"command": "apps", "description": "查看白名单 APP"},
    {"command": "scan", "description": "触发后台扫描"},
    {"command": "help", "description": "使用说明"},
]

def _normalize_user_ids(value) -> set[int]:
    if value is None:
        return set()
    if isinstance(value, str):
        raw = re.split(r"[\s,，;；]+", value.strip())
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = [value]
    ids: set[int] = set()
    for item in raw:
        s = str(item).strip()
        if s and re.fullmatch(r"\d{1,20}", s):
            ids.add(int(s))
    return ids

def load_allowed_user_ids() -> set[int]:
    ids = set()
    ids.update(_normalize_user_ids(_cfg.get("user_ids")))
    ids.update(_normalize_user_ids(_cfg.get("user_id")))
    try:
        main_cfg = json.loads(WL_PATH.read_text())
        notify = main_cfg.get("telegram_notify", {}) or {}
        ids.update(_normalize_user_ids(notify.get("user_ids")))
        ids.update(_normalize_user_ids(notify.get("user_id")))
    except Exception:
        pass
    return ids

def is_allowed_user(user_id: int) -> bool:
    return int(user_id) in load_allowed_user_ids()
IPA_DIR = Path("/data/ipa")
DL_TMP = Path("/data/downloads")
LOG_PATH = Path("/logs/forward-bot.log")

# ===== 日志 =====
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH)]
)
log = logging.getLogger("forward-bot")
logging.getLogger("httpx").setLevel(logging.WARNING)

# ===== 白名单加载 =====
def load_whitelist() -> list:
    try:
        with open(WL_PATH) as f:
            return json.load(f).get("whitelist", [])
    except Exception as e:
        log.error(f"加载白名单失败: {e}")
        return []

def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}

def format_time(value: str | None) -> str:
    if not value:
        return "还没有记录"
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value[:19]

def latest_ipa_files(limit: int = 5) -> list[Path]:
    if not IPA_DIR.exists():
        return []
    return sorted(IPA_DIR.glob("*.ipa"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]

def is_scan_running() -> bool:
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = Path("/proc") / pid / "cmdline"
            text = cmd.read_bytes().replace(b"\0", b" ").decode("utf-8", "ignore")
        except Exception:
            continue
        if "forward_bot.py" in text:
            continue
        if "tg_bot.py" in text or "run-tg-scan.sh" in text:
            return True
    return False

def start_scan_background() -> bool:
    if is_scan_running() or not SCAN_SCRIPT.exists():
        return False
    subprocess.Popen(
        ["/bin/sh", "-lc", "nohup /app/run-tg-scan.sh >/logs/tg-manual-scan.log 2>&1 &"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True

def build_status_text() -> str:
    state = load_state()
    files = latest_ipa_files()
    lines = [
        "🌸 *IPA 小助手状态*",
        "",
        f"📦 仓库：*{len(list(IPA_DIR.glob('*.ipa'))) if IPA_DIR.exists() else 0}* 个 IPA",
        f"🕒 上次扫描：{format_time(state.get('last_scan'))}",
        f"⚙️ 扫描：{'运行中' if is_scan_running() else '空闲'}",
    ]
    if files:
        lines.extend(["", "最近入库："])
        for f in files:
            lines.append(f"🍡 `{f.name}`")
    return "\n".join(lines)

def build_apps_text() -> str:
    whitelist = load_whitelist()
    if not whitelist:
        return "🍵 现在还没有白名单 APP。"
    lines = [f"🎀 *白名单 APP*（{len(whitelist)} 个）", ""]
    for app in whitelist[:35]:
        keywords = ", ".join(app.get("keywords", [])[:4])
        suffix = f" · `{keywords}`" if keywords else ""
        lines.append(f"🍡 *{app.get('name', '未命名')}*{suffix}")
    if len(whitelist) > 35:
        lines.append(f"\n…还有 {len(whitelist) - 35} 个，去 WebUI 看完整列表喔。")
    return "\n".join(lines)

def build_help_text() -> str:
    return (
        "✨ *IPA 小助手菜单*\n\n"
        "/status - 查看仓库和扫描状态\n"
        "/apps - 查看白名单 APP\n"
        "/scan - 触发一次后台扫描\n"
        "/help - 显示这份小菜单\n\n"
        "也可以直接转发 IPA 文件，或发送包含 `.ipa` 文件名和下载链接的文本。"
    )

# ===== IPA 文件名匹配 =====
def match_whitelist(filename: str, message_text: str, whitelist: list) -> str | None:
    text = f"{filename} {message_text or ''}"
    for app in whitelist:
        for kw in app.get("keywords", []):
            if kw.lower() in text.lower():
                return app["name"]
    return None

def extract_version_key(filename: str) -> str:
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    m = re.match(r"^(.+?)_(\d[\d.]*\d)_.*$", name)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    return name

# ===== 仓库检查 =====
def check_warehouse(filename: str) -> dict:
    result = {"exists": False, "same_name": False, "same_version": False,
              "existing_file": None, "existing_ver": None}
    if not IPA_DIR.exists():
        return result
    new_key = extract_version_key(filename)
    for f in sorted(IPA_DIR.glob("*.ipa")):
        if f.name == filename:
            result["exists"] = True
            result["same_name"] = True
            result["existing_file"] = f.name
        if extract_version_key(f.name) == new_key:
            result["exists"] = True
            result["same_version"] = True
            result["existing_ver"] = f.name
    return result

def get_file_size_str(path: Path) -> str:
    size = path.stat().st_size
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"

# ===== Telegram API 直接调用 =====
API_BASE = "https://api.telegram.org"

class TGClient:
    """直接用 httpx 调用 Telegram Bot API，避免 python-telegram-bot 代理问题"""
    def __init__(self, token: str, proxy: str | None = None):
        self.token = token
        self.base = f"{API_BASE}/bot{token}"
        self.file_base = f"{API_BASE}/file/bot{token}"
        kwargs = {"timeout": 30, "follow_redirects": True}
        if proxy:
            kwargs["proxy"] = proxy
        self.client = httpx.AsyncClient(**kwargs)
        self.offset = 0  # getUpdates offset

    async def api(self, method: str, data: dict | None = None) -> dict:
        url = f"{self.base}/{method}"
        resp = await self.client.post(url, json=data)
        resp.raise_for_status()
        result = resp.json()
        if not result.get("ok"):
            raise Exception(f"API error: {result}")
        return result.get("result", {})

    async def get_updates(self) -> list:
        result = await self.api("getUpdates", {"timeout": 10, "offset": self.offset})
        if result:
            self.offset = max(u["update_id"] for u in result) + 1
        return result

    async def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        data = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        return await self.api("sendMessage", data)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> dict:
        data = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        return await self.api("editMessageText", data)

    async def answer_callback_query(self, callback_query_id: str, text: str = "") -> dict:
        return await self.api("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})

    async def set_commands(self) -> dict:
        return await self.api("setMyCommands", {"commands": BOT_COMMANDS})

    async def download_file(self, file_id: str, dest: Path) -> int:
        file_info = await self.api("getFile", {"file_id": file_id})
        file_path = file_info["file_path"]
        url = f"{self.file_base}/{file_path}"
        resp = await self.client.get(url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return len(resp.content)

    async def download_url(self, url: str, dest: Path, proxy: str | None = None) -> int:
        kwargs = {"timeout": 300, "follow_redirects": True}
        if proxy:
            kwargs["proxy"] = proxy
        async with httpx.AsyncClient(**kwargs) as c:
            resp = await c.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return len(resp.content)

    async def close(self):
        await self.client.aclose()

# ===== 内联键盘构建 =====
def make_keyboard(buttons: list[list[dict]]) -> dict:
    return {"inline_keyboard": [[{"text": b["text"], "callback_data": b["data"]} for b in row] for row in buttons]}

# ===== 待处理下载 =====
pending: dict = {}  # key: (chat_id, msg_id) -> info

# ===== 消息处理 =====
async def handle_text_msg(bot: TGClient, chat_id: int, msg_id: int, text: str):
    """处理文本消息：提取 IPA 文件名 + URL"""
    # 优先识别 t.me 单条消息链接 —— 通过 telethon 拉取消息再走文件流程
    tme_match = TME_LINK_RE.search(text or "")
    if tme_match and not re.search(r"\.ipa\b", text, re.IGNORECASE):
        await handle_tme_link(bot, chat_id, msg_id, tme_match.group(1), int(tme_match.group(2)))
        return

    ipa_pattern = re.compile(r'[\w\-.+]+\.ipa', re.IGNORECASE)
    filenames = ipa_pattern.findall(text)
    if not filenames:
        await bot.send_message(chat_id, "⚠️ 未找到 `.ipa` 文件名。\n\n请确保消息包含 IPA 文件名，例如：`Bit远控助手-v3.1.5.ipa`")
        return

    filename = filenames[0]

    # 提取 URL
    urls = re.findall(r'https?://[^\s<>\"]+', text)

    whitelist = load_whitelist()
    app_name = match_whitelist(filename, text, whitelist)
    if not app_name:
        filename_base = re.sub(r'[_-]?\d[\d.]*\d.*', '', filename.rsplit('.', 1)[0]) or filename.rsplit('.', 1)[0]
        try:
            cfg = json.loads(open(WL_PATH).read())
            cfg.setdefault("whitelist", [])
            cfg["whitelist"].append({"name": filename_base, "keywords": [filename_base]})
            from datetime import datetime
            _bak = WL_PATH.with_suffix(WL_PATH.suffix + f".bak-{datetime.now().strftime('%Y%m%d%H%M%S')}")
            shutil.copy2(WL_PATH, _bak)
            with open(WL_PATH, 'w') as fh:
                json.dump(cfg, fh, ensure_ascii=False, indent=2)
            app_name = filename_base
            log.info(f"自动添加白名单: {filename_base}，关键词: ['{filename_base}']")
            await bot.send_message(chat_id,
                f"📝 *自动添加到白名单*\n\nApp：`{filename_base}`\n关键词：`{filename_base}`\n\n继续处理中……")
        except Exception as e:
            log.error(f"自动添加白名单失败: {e}")
            await bot.send_message(chat_id,
                f"⚠️ *不在白名单*\n\n文件：`{filename}`\n\n当前白名单共 {len(whitelist)} 个 app。")
            return

    wh_result = check_warehouse(filename)

    lines = [f"📦 *解析到 IPA*", f"", f"文件：`{filename}`", f"匹配：✅ *{app_name}*"]
    if urls:
        short_url = urls[0][:60] + ("..." if len(urls[0]) > 60 else "")
        lines.append(f"链接：{short_url}")

    if wh_result["exists"]:
        if wh_result["same_name"]:
            lines.append(f"⚠️ 仓库已存在同名文件")
        elif wh_result["same_version"]:
            lines.append(f"⚠️ 仓库已有同版本")
        buttons = [[{"text": "🔄 替换旧版", "data": f"replace|{msg_id}"},
                     {"text": "📁 保留两者", "data": f"keep|{msg_id}"}],
                    [{"text": "❌ 跳过", "data": f"skip|{msg_id}"}]]
    else:
        lines.append(f"✅ 仓库无重复")
        buttons = [[{"text": "✅ 下载入库", "data": f"download|{msg_id}"},
                     {"text": "❌ 跳过", "data": f"skip|{msg_id}"}]]

    sent = await bot.send_message(chat_id, "\n".join(lines), make_keyboard(buttons))
    info = {"filename": filename, "app_name": app_name, "urls": urls,
            "mode": "text", "chat_id": chat_id,
            "caption": text or ""}
    pending[(chat_id, msg_id)] = info
    pending[(chat_id, sent["message_id"])] = info


async def handle_document_msg(bot: TGClient, chat_id: int, msg_id: int, doc: dict, caption: str = "", raw_msg: dict | None = None):
    """处理文件附件消息"""
    filename = doc.get("file_name", "")
    if not filename.lower().endswith(".ipa"):
        await bot.send_message(chat_id, f"❌ 不是 IPA 文件：`{filename}`\n只接受 `.ipa` 后缀。")
        return

    whitelist = load_whitelist()
    app_name = match_whitelist(filename, caption, whitelist)
    if not app_name:
        filename_base = re.sub(r'[_-]?\d[\d.]*\d.*', '', filename.rsplit('.', 1)[0]) or filename.rsplit('.', 1)[0]
        try:
            cfg = json.loads(open(WL_PATH).read())
            cfg.setdefault("whitelist", [])
            cfg["whitelist"].append({"name": filename_base, "keywords": [filename_base]})
            from datetime import datetime
            _bak = WL_PATH.with_suffix(WL_PATH.suffix + f".bak-{datetime.now().strftime('%Y%m%d%H%M%S')}")
            shutil.copy2(WL_PATH, _bak)
            with open(WL_PATH, 'w') as fh:
                json.dump(cfg, fh, ensure_ascii=False, indent=2)
            app_name = filename_base
            log.info(f"自动添加白名单: {filename_base}，关键词: ['{filename_base}']")
            await bot.send_message(chat_id,
                f"📝 *自动添加到白名单*\n\nApp：`{filename_base}`\n关键词：`{filename_base}`\n\n继续处理中……")
        except Exception as e:
            log.error(f"自动添加白名单失败: {e}")
            await bot.send_message(chat_id,
                f"⚠️ *不在白名单*\n\n文件：`{filename}`\n\n当前白名单共 {len(whitelist)} 个 app。")
            return

    wh_result = check_warehouse(filename)
    size_mb = doc.get("file_size", 0) / 1024 / 1024

    # 识别"从频道转发的消息"——这种 file_id 是用户 session 持有的，bot getFile 拿不到；
    # 要改走 telethon 用我们自己的用户 session 去原频道拉附件
    fwd_channel = None
    fwd_msg_id = None
    fwd_label = ""
    if raw_msg:
        # 老字段
        fwd_from_chat = raw_msg.get("forward_from_chat") or {}
        fwd_msg_id = raw_msg.get("forward_from_message_id")
        if fwd_from_chat.get("type") == "channel" and fwd_msg_id:
            fwd_channel = fwd_from_chat.get("username") or fwd_from_chat.get("id")
            fwd_label = fwd_from_chat.get("username") or fwd_from_chat.get("title") or str(fwd_channel)
        # Bot API 7.0+ 新字段 forward_origin
        if not fwd_channel:
            origin = raw_msg.get("forward_origin") or {}
            if origin.get("type") == "channel":
                chat = origin.get("chat") or {}
                fwd_msg_id = origin.get("message_id")
                if fwd_msg_id:
                    fwd_channel = chat.get("username") or chat.get("id")
                    fwd_label = chat.get("username") or chat.get("title") or str(fwd_channel)

    header = "📦 *t.me 转发解析成功*" if fwd_channel else "📦 *收到 IPA 文件*"
    lines = [header, f"",
             f"文件：`{filename}`", f"大小：{size_mb:.1f}MB", f"匹配：✅ *{app_name}*"]
    if fwd_channel:
        lines.append(f"来源：`@{fwd_label}/{fwd_msg_id}`")

    if wh_result["exists"]:
        if wh_result["same_name"]:
            lines.append(f"⚠️ 仓库已存在同名文件")
        elif wh_result["same_version"]:
            lines.append(f"⚠️ 仓库已有同版本")
        buttons = [[{"text": "🔄 替换旧版", "data": f"replace|{msg_id}"},
                     {"text": "📁 保留两者", "data": f"keep|{msg_id}"}],
                    [{"text": "❌ 跳过", "data": f"skip|{msg_id}"}]]
    else:
        lines.append(f"✅ 仓库无重复")
        buttons = [[{"text": "✅ 下载入库", "data": f"download|{msg_id}"},
                     {"text": "❌ 跳过", "data": f"skip|{msg_id}"}]]

    sent = await bot.send_message(chat_id, "\n".join(lines), make_keyboard(buttons))
    if fwd_channel:
        info = {"filename": filename, "app_name": app_name,
                "mode": "tme", "chat_id": chat_id,
                "tme_channel": fwd_channel, "tme_msg_id": int(fwd_msg_id),
                "caption": caption or ""}
    else:
        info = {"filename": filename, "app_name": app_name, "file_id": doc["file_id"],
                "mode": "document", "chat_id": chat_id,
                "caption": caption or ""}
    pending[(chat_id, msg_id)] = info
    pending[(chat_id, sent["message_id"])] = info


def _build_proxy_tuple():
    """把 PRX_URL 转成 telethon 兼容的 proxy 元组"""
    if not PRX_URL:
        return None
    try:
        p = urlparse(PRX_URL)
        scheme = (p.scheme or "").lower()
        host = p.hostname
        port = p.port or (7890 if "http" in scheme else 1080)
        if not host:
            return None
        if scheme.startswith("socks"):
            try:
                import socks  # type: ignore
                proxy_type = socks.SOCKS5 if scheme == "socks5" else socks.SOCKS4
                return (proxy_type, host, port)
            except Exception:
                return None
        # 默认按 http 代理走 telethon python-socks 后端
        return ("http", host, port)
    except Exception:
        return None


async def _telethon_client():
    """构造一个临时 telethon 客户端，使用与 tg_bot 同一 session"""
    from telethon import TelegramClient
    cfg_main = {}
    try:
        cfg_main = json.loads(WL_PATH.read_text())
    except Exception:
        pass
    api_id = cfg_main.get("api_id")
    api_hash = cfg_main.get("api_hash")
    if not api_id or not api_hash:
        raise RuntimeError("config.json 缺少 api_id/api_hash")
    client = TelegramClient(
        str(SESSION_PATH), int(api_id), str(api_hash),
        device_model="ipaes", system_version="1.0",
        proxy=_build_proxy_tuple(),
    )
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("Telethon session 未登录，请在 Web UI 完成登录")
    return client


async def handle_tme_link(bot: TGClient, chat_id: int, msg_id: int, channel: str, link_msg_id: int):
    """通过 telethon 拉取 t.me/<channel>/<msg_id> 的消息并处理 IPA 附件"""
    await bot.send_message(chat_id, f"🔗 正在拉取频道消息：`@{channel}/{link_msg_id}`")
    try:
        client = await _telethon_client()
    except Exception as e:
        log.error(f"telethon 连接失败: {e!r}")
        await bot.send_message(chat_id, f"❌ 无法连接 Telegram：{str(e)[:120]}")
        return

    try:
        try:
            entity = await client.get_entity(channel)
        except Exception as e:
            await bot.send_message(chat_id, f"❌ 找不到频道 `@{channel}`：{str(e)[:100]}")
            return
        try:
            msg = await client.get_messages(entity, ids=link_msg_id)
        except Exception as e:
            await bot.send_message(chat_id, f"❌ 读取消息失败：{str(e)[:100]}")
            return
        if not msg:
            await bot.send_message(chat_id, "❌ 这条消息不存在或已删除")
            return

        doc = getattr(msg, "document", None)
        filename = ""
        size_bytes = 0
        if doc:
            for attr in getattr(doc, "attributes", []) or []:
                if getattr(attr, "file_name", None):
                    filename = attr.file_name
                    break
            size_bytes = getattr(doc, "size", 0) or 0
        caption = (msg.message or "") if msg else ""

        if not filename or not filename.lower().endswith(".ipa"):
            # 退回：用消息文字走文本流程
            if caption:
                await handle_text_msg(bot, chat_id, msg_id, caption)
            else:
                await bot.send_message(chat_id, "❌ 这条消息没有 IPA 附件，也没有可解析的文本")
            return

        whitelist = load_whitelist()
        app_name = match_whitelist(filename, caption, whitelist)
        if not app_name:
            filename_base = re.sub(r'[_-]?\d[\d.]*\d.*', '', filename.rsplit('.', 1)[0]) or filename.rsplit('.', 1)[0]
            try:
                cfg = json.loads(open(WL_PATH).read())
                cfg.setdefault("whitelist", [])
                cfg["whitelist"].append({"name": filename_base, "keywords": [filename_base]})
                _bak = WL_PATH.with_suffix(WL_PATH.suffix + f".bak-{datetime.now().strftime('%Y%m%d%H%M%S')}")
                shutil.copy2(WL_PATH, _bak)
                with open(WL_PATH, 'w') as fh:
                    json.dump(cfg, fh, ensure_ascii=False, indent=2)
                app_name = filename_base
                await bot.send_message(chat_id,
                    f"📝 *自动添加到白名单*\n\nApp：`{filename_base}`\n关键词：`{filename_base}`")
            except Exception as e:
                await bot.send_message(chat_id, f"⚠️ *不在白名单*\n\n文件：`{filename}`")
                return

        wh_result = check_warehouse(filename)
        size_mb = size_bytes / 1024 / 1024 if size_bytes else 0

        lines = [f"📦 *t.me 链接解析成功*", "",
                 f"文件：`{filename}`",
                 f"大小：{size_mb:.1f}MB" if size_mb else "大小：未知",
                 f"匹配：✅ *{app_name}*"]
        if wh_result["exists"]:
            if wh_result["same_name"]:
                lines.append("⚠️ 仓库已存在同名文件")
            elif wh_result["same_version"]:
                lines.append("⚠️ 仓库已有同版本")
            buttons = [[{"text": "🔄 替换旧版", "data": f"replace|{msg_id}"},
                        {"text": "📁 保留两者", "data": f"keep|{msg_id}"}],
                       [{"text": "❌ 跳过", "data": f"skip|{msg_id}"}]]
        else:
            lines.append("✅ 仓库无重复")
            buttons = [[{"text": "✅ 下载入库", "data": f"download|{msg_id}"},
                        {"text": "❌ 跳过", "data": f"skip|{msg_id}"}]]

        sent = await bot.send_message(chat_id, "\n".join(lines), make_keyboard(buttons))
        info = {"filename": filename, "app_name": app_name,
                "mode": "tme", "chat_id": chat_id,
                "tme_channel": channel, "tme_msg_id": link_msg_id,
                "caption": caption or ""}
        pending[(chat_id, msg_id)] = info
        pending[(chat_id, sent["message_id"])] = info
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def _telethon_download_message(channel: str, link_msg_id: int, dest_path: Path):
    """临时连 telethon，下载指定 t.me 消息的附件到 dest_path"""
    client = await _telethon_client()
    try:
        entity = await client.get_entity(channel)
        msg = await client.get_messages(entity, ids=link_msg_id)
        if not msg or not getattr(msg, "document", None):
            raise RuntimeError("消息已不可用或不含附件")
        await client.download_media(msg, file=str(dest_path))
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def handle_callback(bot: TGClient, cb_id: str, chat_id: int, msg_id: int, data: str):
    """处理按钮回调"""
    action, ref_str = data.split("|", 1)
    ref_msg_id = int(ref_str)

    info = pending.pop((chat_id, ref_msg_id), None)
    if not info:
        await bot.answer_callback_query(cb_id, "信息已过期，请重新转发")
        return

    # 立刻 ack 一次（callback 必须 ~15s 内响应；长下载放后面执行）
    try:
        await bot.answer_callback_query(cb_id, "已收到")
    except Exception as _e:
        log.warning(f"early answer_callback_query 失败（忽略）: {_e!r}")

    filename = info["filename"]
    app_name = info["app_name"]

    if action == "skip":
        await bot.edit_message_text(chat_id, msg_id, f"⏭️ *已跳过*\n\n文件：`{filename}`")
        return

    # 正在下载
    await bot.edit_message_text(chat_id, msg_id, f"📥 收到，正在下载……\n\n文件：`{filename}`")

    try:
        DL_TMP.mkdir(parents=True, exist_ok=True)
        tmp_path = DL_TMP / filename

        if info["mode"] == "text":
            urls = info.get("urls", [])
            if not urls:
                await bot.edit_message_text(chat_id, msg_id, f"❌ 未找到下载链接\n\n文件：`{filename}`")
                return
            proxy = None
            if PRX_URL:
                p = urlparse(PRX_URL)
                proxy = f"{p.scheme}://{p.hostname}:{p.port or 1080}"
            await bot.download_url(urls[0], tmp_path, proxy)
        elif info["mode"] == "tme":
            await bot.edit_message_text(chat_id, msg_id,
                f"📥 通过 Telegram 拉取附件中（可能要 1-3 分钟）……\n\n文件：`{filename}`")
            await _telethon_download_message(info["tme_channel"], info["tme_msg_id"], tmp_path)
        else:
            await bot.download_file(info["file_id"], tmp_path)

        actual_size = tmp_path.stat().st_size
        log.info(f"下载完成: {filename} ({actual_size} bytes)")
    except Exception as e:
        err_msg = str(e)[:200]
        log.error(f"下载失败: {filename} -> {err_msg}")
        if "400" in err_msg and "getFile" in err_msg:
            await bot.edit_message_text(chat_id, msg_id,
                f"❌ *下载失败*\n\n文件：`{filename}`\n\nTG 文件可能已过期。请尝试用文本消息发送包含 `.ipa` 文件名+链接的格式。")
        else:
            await bot.edit_message_text(chat_id, msg_id,
                f"❌ *下载失败*\n\n文件：`{filename}`\n错误：{err_msg}")
        try:
            await bot.answer_callback_query(cb_id, "下载失败")
        except Exception:
            pass
        return

    # 入库中
    await bot.edit_message_text(chat_id, msg_id, f"⬇️ 已下载完成，正在入库……\n\n文件：`{filename}`")

    deleted = []
    if action == "replace":
        new_key = extract_version_key(filename)
        for old_f in sorted(IPA_DIR.glob("*.ipa")):
            if old_f.name == filename:
                old_f.unlink()
                deleted.append(old_f.name)
            elif extract_version_key(old_f.name) == new_key:
                old_f.unlink()
                deleted.append(old_f.name)

    dest_path = IPA_DIR / filename
    if dest_path.exists():
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        stem = filename.rsplit(".", 1)[0]
        dest_path = IPA_DIR / f"{stem}_{ts}.ipa"
    shutil.move(str(tmp_path), str(dest_path))

    # 记录"破解点 / 版本说明"——在 scanner.py 重建 repo.json 之前写入，
    # 这样这次重建就能拿到最新的 highlights。
    try:
        caption = info.get("caption") or ""
        source_url = ""
        if info.get("mode") == "tme" and info.get("tme_channel") and info.get("tme_msg_id"):
            ch = str(info["tme_channel"]).lstrip("@")
            source_url = f"https://t.me/{ch}/{info['tme_msg_id']}"
        ipa_desc.remember_from_message(dest_path.name, caption, source_url)
    except Exception as _e:
        log.warning(f"记录 IPA 描述失败: {_e}")

    # 重建订阅源
    await bot.edit_message_text(chat_id, msg_id, f"📦 正在重建订阅源……\n\n文件：`{dest_path.name}`")
    time.sleep(5)
    scan_ok = True
    try:
        import subprocess
        subprocess.run([sys.executable, "/app/scanner.py"], capture_output=True, timeout=120)
    except Exception as e:
        log.error(f"scanner 失败: {e}")
        scan_ok = False

    status = "已替换" if deleted else "已入库"
    lines = [f"✅ *{status}！*", f"",
             f"文件：`{dest_path.name}`", f"大小：{actual_size / 1024 / 1024:.1f}MB", f"匹配：{app_name}"]
    if deleted:
        lines.append(f"")
        for d in deleted:
            lines.append(f"  🗑️ `{d}`")
    if scan_ok:
        lines.append(f"")
        lines.append(f"📡 订阅源已自动更新 ✅")
    await bot.edit_message_text(chat_id, msg_id, "\n".join(lines))
    try:
        await bot.answer_callback_query(cb_id, status)
    except Exception:
        pass


# ===== 主轮询循环 =====
async def poll_loop(bot: TGClient):
    """自己管理 getUpdates 轮询"""
    log.info("轮询启动...")
    while True:
        try:
            updates = await bot.get_updates()
            for u in updates:
                try:
                    await process_update(bot, u)
                except Exception as e:
                    log.error(f"处理 update 失败: {e}")
        except Exception as e:
            log.error(f"getUpdates 失败: {e}")
            await asyncio.sleep(5)  # 出错后等5秒重试
            continue
        await asyncio.sleep(2)  # 每2秒轮询一次


async def process_update(bot: TGClient, update: dict):
    """分发处理 update"""
    # 回调查询
    if "callback_query" in update:
        cb = update["callback_query"]
        chat_id = cb["message"]["chat"]["id"]
        msg_id = cb["message"]["message_id"]
        cb_id = cb["id"]
        user_id = cb["from"]["id"]
        data = cb.get("data", "")
        if not is_allowed_user(user_id):
            await bot.answer_callback_query(cb_id, "无权操作")
            return
        await handle_callback(bot, cb_id, chat_id, msg_id, data)
        return

    # 普通消息
    msg = update.get("message")
    if not msg:
        return

    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    if not is_allowed_user(user_id):
        return

    msg_id = msg["message_id"]

    # 菜单命令
    text = msg.get("text", "")
    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0].lower()
        if cmd == "/start":
            await bot.send_message(chat_id,
                "🌸 *IPA 小助手已就绪*\n\n"
                "转发 IPA 文件或包含 `.ipa` 文件名和链接的文本给我。\n"
                "我会帮你匹配白名单、检查重复，然后让你选择入库方式。\n\n"
                "菜单已经准备好啦，きらきら～ ✨")
        elif cmd == "/status":
            await bot.send_message(chat_id, build_status_text())
        elif cmd == "/apps":
            await bot.send_message(chat_id, build_apps_text())
        elif cmd == "/scan":
            if is_scan_running():
                await bot.send_message(chat_id, "🍵 已经有扫描在后台跑啦，等它完成就好。")
            elif start_scan_background():
                await bot.send_message(chat_id, "🚀 已触发后台扫描。完成后如果有新版本入库，会给你发汇总通知。")
            else:
                await bot.send_message(chat_id, "⚠️ 没能启动扫描，请确认 `/app/run-tg-scan.sh` 是否存在。")
        elif cmd == "/help":
            await bot.send_message(chat_id, build_help_text())
        else:
            await bot.send_message(chat_id, "🍡 暂时不认识这个命令，发送 /help 看一下菜单吧。")
        return

    # 文件附件
    if "document" in msg:
        doc = msg["document"]
        caption = msg.get("caption", "")
        await handle_document_msg(bot, chat_id, msg_id, doc, caption, msg)
        return

    # 文本消息
    if text and not text.startswith("/"):
        await handle_text_msg(bot, chat_id, msg_id, text)


async def main():
    if not BOT_TK:
        log.error("未找到配置！请确保 /config/forward_bot.json 存在")
        sys.exit(1)
    allowed_user_ids = load_allowed_user_ids()
    if not allowed_user_ids:
        log.error("未设置 USER_ID 白名单！")
        sys.exit(1)

    allowed_text = ', '.join(map(str, sorted(allowed_user_ids)))
    log.info(f"IPA 转发 Bot 启动，授权用户: {allowed_text}")

    proxy = None
    if PRX_URL:
        p = urlparse(PRX_URL)
        proxy = f"{p.scheme}://{p.hostname}:{p.port or 1080}"
        log.info(f"使用代理: {proxy}")

    bot = TGClient(BOT_TK, proxy)

    # 验证连接
    me = await bot.api("getMe")
    log.info(f"Bot 已连接: {me.get('first_name')} (@{me.get('username')})")

    # 删除 webhook（如果有的话）
    await bot.api("deleteWebhook")
    await bot.set_commands()
    log.info("Bot 菜单已同步")

    try:
        await poll_loop(bot)
    finally:
        await bot.close()

if __name__ == "__main__":
    asyncio.run(main())
