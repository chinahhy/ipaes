#!/usr/bin/env python3
"""
Telegram IPA Scanner - cron 触发，按白名单下载IPA
配置全部来自 /config/config.json + 环境变量

环境变量:
  TG_PROXY      - socks5://host:port 或 http://host:port，可空（直连）
  TG_SCAN_HOURS - 回溯小时数（默认25）
  TG_SCAN_LIMIT - 每个群最多读取消息数（默认500）
"""
import asyncio, json, logging, os, random, re, sys, zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

from telethon import TelegramClient
from telethon.errors import FloodWaitError, PeerFloodError
from telethon.tl.types import DocumentAttributeFilename

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ipa_descriptions as ipa_desc

CONFIG_PATH = Path("/config/config.json")
FORWARD_BOT_CONFIG_PATH = Path("/config/forward_bot.json")
PROXY_CONFIG_PATH = Path("/config/proxy.json")
SESSION_PATH = Path("/session/tg-ipa-bot")
LOG_PATH = Path(os.environ.get("TG_LOG_PATH", "/logs/tg-cron.log"))
DOWNLOAD_DIR = Path("/data/ipa")
STATE_PATH = Path("/config/state.json")

BOT_COMMANDS = [
    {"command": "start", "description": "打开 IPA 小助手"},
    {"command": "status", "description": "查看仓库状态"},
    {"command": "apps", "description": "查看白名单 APP"},
    {"command": "scan", "description": "触发后台扫描"},
    {"command": "help", "description": "使用说明"},
]

# 解析代理环境变量 socks5/http URL → Telethon 兼容 proxy tuple。
def parse_proxy(proxy_url: str):
    if not proxy_url:
        return None
    p = urlparse(proxy_url)
    scheme = (p.scheme or "socks5").lower()
    host = p.hostname
    if not host:
        return None
    port = p.port or (7890 if scheme.startswith("http") else 1080)
    username = unquote(p.username) if p.username else None
    password = unquote(p.password) if p.password else None

    proxy_type = scheme
    try:
        import socks  # type: ignore
        if scheme in ("socks5", "socks5h"):
            proxy_type = socks.SOCKS5
        elif scheme == "socks4":
            proxy_type = socks.SOCKS4
        elif scheme in ("http", "https"):
            proxy_type = socks.HTTP
    except Exception:
        if scheme == "https":
            proxy_type = "http"

    if username or password:
        return (proxy_type, host, port, True, username, password)
    return (proxy_type, host, port)

def redact_proxy_url(proxy_url: str):
    if not proxy_url:
        return ""
    try:
        p = urlparse(proxy_url)
        if not (p.username or p.password):
            return proxy_url
        host = p.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = f"***@{host}"
        if p.port:
            netloc += f":{p.port}"
        return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))
    except Exception:
        return "<invalid proxy url>"

def load_proxy_url():
    if PROXY_CONFIG_PATH.exists():
        try:
            cfg = json.loads(PROXY_CONFIG_PATH.read_text())
            url = str(cfg.get("url") or "").strip()
            if cfg.get("enabled", True) and url:
                return url
        except Exception:
            pass
    return os.environ.get("TG_PROXY", "")

PROXY_URL = load_proxy_url()
PROXY = parse_proxy(PROXY_URL)
SCAN_HOURS = int(os.environ.get("TG_SCAN_HOURS", "25"))
SCAN_LIMIT = int(os.environ.get("TG_SCAN_LIMIT", "500"))
DOWNLOAD_TIMEOUT = int(os.environ.get("TG_DOWNLOAD_TIMEOUT", "1800"))  # 单文件下载超时（秒），默认30分钟
MAX_CONCURRENT_DOWNLOADS = int(os.environ.get("TG_MAX_CONCURRENT", "3"))  # 最大并发下载数

log = logging.getLogger("tg-bot")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_file_handler = logging.FileHandler(LOG_PATH)
_file_handler.setFormatter(_formatter)
log.setLevel(logging.INFO)
log.handlers.clear()
log.addHandler(_file_handler)
log.propagate = False

# Telethon 在跨 DC 下载/代理抖动时会刷大量底层重连 traceback。
# 业务层已经记录“下载失败/扫描总结”，这里压低第三方库噪音，防止 tg-cron.log 几小时膨胀到几百 MB。
logging.getLogger().handlers.clear()
logging.getLogger().setLevel(logging.CRITICAL)
for _name in ("telethon", "asyncio"):
    logging.getLogger(_name).handlers.clear()
    logging.getLogger(_name).setLevel(logging.CRITICAL)
    logging.getLogger(_name).propagate = False


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_forward_bot_config():
    if not FORWARD_BOT_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(FORWARD_BOT_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def normalize_user_ids(value):
    if value is None:
        return []
    if isinstance(value, str):
        raw = re.split(r"[\s,，;；]+", value.strip())
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = [value]
    ids, seen = [], set()
    for item in raw:
        s = str(item).strip()
        if s and re.fullmatch(r"\d{1,20}", s) and s not in seen:
            seen.add(s)
            ids.append(s)
    return ids

def notification_bot_config(config):
    notify = dict(config.get("telegram_notify") or {})
    forward = load_forward_bot_config()
    token = str(notify.get("bot_token") or forward.get("bot_token") or "").strip()
    chat_ids = []
    chat_ids.extend(normalize_user_ids(notify.get("chat_id")))
    chat_ids.extend(normalize_user_ids(notify.get("chat_ids")))
    chat_ids.extend(normalize_user_ids(notify.get("user_ids")))
    chat_ids.extend(normalize_user_ids(notify.get("user_id")))
    chat_ids.extend(normalize_user_ids(forward.get("user_ids")))
    chat_ids.extend(normalize_user_ids(forward.get("user_id")))
    deduped = []
    for chat_id in chat_ids:
        if chat_id not in deduped:
            deduped.append(chat_id)
    return {
        "enabled": bool(token) and notify.get("enabled", True) is not False,
        "token": token,
        "chat_ids": deduped,
    }

async def telegram_bot_api(token, method, payload):
    import httpx
    proxy_url = load_proxy_url()
    async with httpx.AsyncClient(proxy=proxy_url or None, timeout=18, follow_redirects=True) as client:
        resp = await client.post(f"https://api.telegram.org/bot{token}/{method}", json=payload)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description") or str(data))
        return data.get("result")

async def ensure_bot_menu(config):
    bot = notification_bot_config(config)
    if not bot["enabled"]:
        return False
    try:
        await telegram_bot_api(bot["token"], "setMyCommands", {"commands": BOT_COMMANDS})
        return True
    except Exception as e:
        log.warning(f"Bot 菜单设置失败: {e}")
        return False

def version_label(filename):
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    m = re.match(r"^.+?_(\d[\d.]*\d)_.*$", name)
    if m:
        return "v" + m.group(1)
    return "新版本"

def compact_text(value, limit=76):
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"

def build_notification_text(
    downloaded, total_ipa, total_dl, total_skipped, errors_count,
    groups_count=0, total_msgs=0,
):
    scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "🚀 有新包入库" if total_dl else "🟢 仓库已是最新"
    lines = [
        "📦 IPAes 扫描报告",
        f"🕐 {scan_time}",
        "",
        f"状态：{status}",
        f"扫描：{groups_count} 个群 / {total_msgs} 条消息",
        f"结果：发现 {total_ipa} 个 IPA / 入库 {total_dl} 个 / 跳过 {total_skipped} 个 / 异常 {errors_count} 个",
    ]

    if downloaded:
        lines.extend(["", "🎁 新鲜上架"])
        for idx, item in enumerate(downloaded[:8], start=1):
            lines.append(
                f"{idx}. {compact_text(item['app'], 24)} · {version_label(item['filename'])} · {item['size_mb']} MB"
            )
            lines.append(f"   {compact_text(item['filename'], 72)}")
        if len(downloaded) > 8:
            lines.append(f"   ...还有 {len(downloaded) - 8} 个新包，详情看 tg-cron.log")
    else:
        lines.extend([
            "",
            "🧊 本轮没有新包",
            "白名单命中的版本都已经在库里；这次只是例行巡检。"
        ])

    if errors_count:
        lines.extend([
            "",
            f"⚠️ 有 {errors_count} 个异常项，建议查看 /logs/tg-cron.log。"
        ])

    lines.extend(["", "下次仍按 TG_SCAN_CRON 自动巡检。"])
    return "\n".join(lines)

async def send_scan_notification(config, all_results, total_ipa, total_dl, total_skipped):
    downloaded = [item for result in all_results for item in result.get("downloaded", [])]
    errors_count = sum(len(result.get("errors", [])) for result in all_results)
    total_msgs = sum(result.get("total_msgs", 0) for result in all_results)
    log.info(
        "通知准备: total_ipa=%s, total_dl=%s, total_skipped=%s, errors=%s, downloaded_count=%s",
        total_ipa, total_dl, total_skipped, errors_count, len(downloaded),
    )
    bot = notification_bot_config(config)
    log.info(
        "Bot 通知配置: enabled=%s, has_token=%s, chat_ids=%s",
        bot["enabled"], bool(bot.get("token")), bot.get("chat_ids"),
    )
    if not bot["enabled"] or not bot["chat_ids"]:
        log.warning("Bot 通知未发送：未配置 bot_token 或 USER_ID/chat_id")
        return
    try:
        await ensure_bot_menu(config)
    except Exception as e:
        log.warning(f"同步 Bot 菜单失败（忽略，继续发通知）: {e!r}")
    text = build_notification_text(
        downloaded, total_ipa, total_dl, total_skipped, errors_count,
        groups_count=len(all_results), total_msgs=total_msgs,
    )
    for chat_id in bot["chat_ids"]:
        try:
            await telegram_bot_api(bot["token"], "sendMessage", {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            })
            log.info(f"Bot 通知已发送: {chat_id}")
        except Exception as e:
            log.warning(f"Bot 通知发送失败 {chat_id}: {e!r}")

def load_state():
    if not STATE_PATH.exists():
        return {"downloaded_files": [], "downloaded_versions": {}, "last_scan": None}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def match_whitelist(filename, message_text, whitelist):
    text = f"{filename} {message_text or ''}"
    for app in whitelist:
        for kw in app["keywords"]:
            if kw.lower() in text.lower():
                return app["name"]
    return None

def extract_version_key(filename):
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    m = re.match(r"^(.+?)_(\d[\d.]*\d)_.*$", name)
    if m: return f"{m.group(1)}_{m.group(2)}"
    return name

def get_filename(message):
    if not message.document: return None
    for attr in message.document.attributes:
        if isinstance(attr, DocumentAttributeFilename):
            return attr.file_name
    return None


# 同 grouped_id 上下文窗口大小（一次 album 通常 2~10 条；给 20 富裕）
ALBUM_LOOKAROUND = 20


async def resolve_caption(client, entity, message) -> str:
    """取消息的 caption；若本消息为空且属于 album，则取同 grouped_id 兄弟里
    最长的那条 caption。

    Telegram album（grouped media）中只有一条消息携带 caption，其它都是
    空。我们的 IPA 文件可能在前面任意一条上，直接读 message.text 会漏。
    """
    text = (getattr(message, "text", None) or getattr(message, "message", None) or "").strip()
    if text:
        return text
    gid = getattr(message, "grouped_id", None)
    if not gid:
        return ""
    try:
        # 在 message.id 前后各取窗口内的消息
        ids = list(range(max(1, message.id - ALBUM_LOOKAROUND),
                         message.id + ALBUM_LOOKAROUND + 1))
        siblings = await client.get_messages(entity, ids=ids)
    except Exception as e:
        log.warning(f"  取 album 兄弟消息失败 ({message.id}): {e!r}")
        return ""
    best = ""
    for s in siblings or []:
        if not s or s.id == message.id:
            continue
        if getattr(s, "grouped_id", None) != gid:
            continue
        t = (getattr(s, "text", None) or getattr(s, "message", None) or "").strip()
        if t and len(t) > len(best):
            best = t
    return best

def safe_filename(name):
    name = re.sub(r"[/\\:*?\"<>|]", "_", name)
    return name[:200]

def validate_ipa(path: Path, expected_size: int | None = None):
    try:
        if expected_size is not None and path.stat().st_size != expected_size:
            return False, f"文件大小不完整 {path.stat().st_size}/{expected_size}"
        if not zipfile.is_zipfile(path):
            return False, "不是有效 zip/ipa 文件"
        with zipfile.ZipFile(path) as zf:
            if not any(
                len(n.split("/")) == 3 and
                n.startswith("Payload/") and
                n.endswith(".app/Info.plist")
                for n in zf.namelist()
            ):
                return False, "缺少 Payload/*.app/Info.plist"
            bad = zf.testzip()
            if bad:
                return False, f"zip 条目损坏: {bad}"
        return True, ""
    except Exception as e:
        return False, str(e)


async def scan_group(client, group_link, hours_back, whitelist, state,
                     rate_limit, priority_versions, priority_groups):
    """扫描群组，收集待下载 IPA 列表（不下载，下载由 main 统一并发执行）。"""
    result = {"group": group_link, "total_msgs": 0, "ipa_found": 0,
              "downloaded": [], "skipped": 0, "errors": [], "version_keys": set(),
              "pending": []}

    try:
        entity = await client.get_entity(group_link)
        group_title = getattr(entity, "title", group_link)
        result["group_title"] = group_title
    except Exception as e:
        log.error(f"无法访问群 {group_link}: {e}")
        result["errors"].append(f"无法访问: {e}")
        return result

    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    log.info(f"扫描群 [{group_title}]，回溯{hours_back}小时")

    downloaded_set = set(state["downloaded_files"])
    today_str = datetime.now().strftime("%Y%m%d")
    today_count = sum(1 for f in state["downloaded_files"] if f.startswith(today_str))
    is_priority = group_link in priority_groups

    # 同一 album 内的 caption 解析做一次缓存，避免对每个 IPA 都打一次额外
    # 的 get_messages。grouped_id 为 None 时不缓存。
    album_caption_cache: dict = {}

    async def _get_caption(msg):
        text = (getattr(msg, "text", None) or getattr(msg, "message", None) or "").strip()
        if text:
            return text
        gid = getattr(msg, "grouped_id", None)
        if gid is None:
            return ""
        if gid in album_caption_cache:
            return album_caption_cache[gid]
        cap = await resolve_caption(client, entity, msg)
        album_caption_cache[gid] = cap
        return cap

    async for message in client.iter_messages(entity, offset_date=None, reverse=False, limit=SCAN_LIMIT):
        if message.date < since: break
        result["total_msgs"] += 1

        filename = get_filename(message)
        if not filename or not filename.lower().endswith(".ipa"): continue
        result["ipa_found"] += 1

        # caption 可能挂在同 album 的兄弟消息上；这里用解析后的 caption 同时
        # 用于白名单匹配与后续 ipa_desc 写入
        caption_text = await _get_caption(message)
        app_name = match_whitelist(filename, caption_text, whitelist)
        if not app_name:
            log.info(f"  跳过（不在白名单）: {filename}")
            result["skipped"] += 1; continue

        size = message.document.size
        unique_key = f"{safe_filename(filename)}_{size}"
        ver_key = extract_version_key(filename)
        save_path = DOWNLOAD_DIR / safe_filename(filename)
        part_path = save_path.with_name(save_path.name + ".part")

        if save_path.exists():
            ok, reason = validate_ipa(save_path, size)
            if ok:
                log.info(f"  跳过（本地已有完整文件）: {filename}")
                downloaded_set.add(unique_key)
                if unique_key not in state["downloaded_files"]:
                    state["downloaded_files"].append(unique_key)
                result["version_keys"].add(ver_key)
                continue
            log.warning(f"  删除残缺文件后重下: {filename} ({reason})")
            try:
                save_path.unlink()
            except OSError:
                pass
        elif unique_key in downloaded_set:
            log.warning(f"  状态已记录但本地无完整文件，重新下载: {filename}")

        if not is_priority and ver_key in priority_versions:
            log.info(f"  跳过（优先群已有同版本）: {filename}")
            result["skipped"] += 1; continue

        if today_count >= rate_limit["max_per_day"]:
            log.warning(f"  已达今日上限{rate_limit['max_per_day']}")
            break

        if part_path.exists():
            try:
                part_path.unlink()
            except OSError:
                pass

        # 收集到待下载列表，不在这里下载
        result["pending"].append({
            "message": message, "filename": filename, "app_name": app_name,
            "size": size, "unique_key": unique_key, "ver_key": ver_key,
            "save_path": save_path, "part_path": part_path,
            "caption_text": caption_text, "group_link": group_link,
            "group_title": group_title, "message_id": message.id,
        })
        log.info(f"  待下载: [{app_name}] {filename} ({size/1024/1024:.1f}MB)")

    return result


async def download_one(client, item, state, semaphore, stop_event):
    """并发下载单个 IPA。

    FloodWait/PeerFlood → 设置 stop_event 阻止后续下载并 raise（由 main 捕获）。
    其他错误（超时/校验失败）→ 返回 error dict，不影响其他下载。
    """
    if stop_event.is_set():
        return {"status": "cancelled", "item": item}

    async with semaphore:
        if stop_event.is_set():
            return {"status": "cancelled", "item": item}

        filename = item["filename"]
        part_path = item["part_path"]
        save_path = item["save_path"]

        # 并发场景下可能另一个任务刚下完同名文件
        if save_path.exists():
            ok, _ = validate_ipa(save_path, item["size"])
            if ok:
                log.info(f"  跳过（已被并发任务下载）: {filename}")
                return {"status": "skipped", "item": item}

        log.info(f"  下载: [{item['app_name']}] {filename} ({item['size']/1024/1024:.1f}MB)")
        try:
            await asyncio.wait_for(
                client.download_media(item["message"], file=str(part_path)),
                timeout=DOWNLOAD_TIMEOUT,
            )
            ok, reason = validate_ipa(part_path, item["size"])
            if not ok:
                raise RuntimeError(reason)
            part_path.replace(save_path)

            unique_key = item["unique_key"]
            if unique_key not in state["downloaded_files"]:
                state["downloaded_files"].append(unique_key)
            state.setdefault("downloaded_versions", {})[item["ver_key"]] = item["group_link"]

            # 记录"破解点 / 版本说明"
            try:
                gl = item["group_link"]
                source_url = f"{gl.rstrip('/')}/{item['message_id']}" if gl.startswith("http") else ""
                ipa_desc.remember_from_message(save_path.name, item["caption_text"], source_url)
            except Exception as _e:
                log.warning(f"  记录 IPA 描述失败: {_e}")

            log.info(f"  OK: {save_path.name}")
            return {"status": "ok", "item": item}

        except FloodWaitError as e:
            log.error(f"  FloodWait! 需等{e.seconds}s，紧急停机")
            stop_event.set()
            raise
        except PeerFloodError as e:
            log.error(f"  PeerFlood! 账号可能被风控")
            stop_event.set()
            raise
        except asyncio.TimeoutError:
            try:
                part_path.unlink()
            except OSError:
                pass
            log.error(f"  下载超时（{DOWNLOAD_TIMEOUT}秒）: {filename}")
            return {"status": "timeout", "item": item}
        except Exception as e:
            try:
                part_path.unlink()
            except OSError:
                pass
            log.error(f"  下载失败: {e}")
            return {"status": "error", "item": item, "error": str(e)}


async def main():
    config = load_config()
    state = load_state()
    api_id = config["api_id"]
    api_hash = config["api_hash"]
    groups = config["groups"]
    whitelist = config["whitelist"]
    rate_limit = config.get("rate_limit", {
        "min_interval_sec": 30, "max_interval_sec": 120, "max_per_day": 50
    })
    priority_groups = config.get("priority_groups", [])

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True) if not DOWNLOAD_DIR.exists() else None
    log.info("=" * 60)
    log.info(f"TG扫描开始，回溯{SCAN_HOURS}小时，每群最多{SCAN_LIMIT}条消息")
    if PROXY_URL:
        log.info(f"代理URL: {redact_proxy_url(PROXY_URL)}")
        log.info(f"代理解析: {PROXY or '无效，按直连处理'}")
    else:
        log.info("代理: 直连")

    client = TelegramClient(
        str(SESSION_PATH), api_id, api_hash,
        device_model="ipaes", system_version="1.0",
        proxy=PROXY,
    )

    try:
        await client.connect()
        if not await client.is_user_authorized():
            log.error("Session未授权！请运行 /app/tg-login.sh")
            sys.exit(2)

        me = await client.get_me()
        log.info(f"已登录: {me.first_name} (@{me.username or 'N/A'})")

        # 排序群组：优先群在前
        sorted_groups = [g for g in priority_groups if g in groups]
        for g in groups:
            if g not in sorted_groups: sorted_groups.append(g)

        priority_versions = set()
        if "downloaded_versions" in state:
            for vk in state["downloaded_versions"].keys():
                priority_versions.add(vk)

        # ---- 扫描阶段：遍历所有群，收集待下载列表 ----
        all_results = []
        for group in sorted_groups:
            try:
                result = await scan_group(client, group, SCAN_HOURS, whitelist,
                                          state, rate_limit, priority_versions,
                                          priority_groups)
                all_results.append(result)
                if group in priority_groups:
                    for vk in result.get("version_keys", set()):
                        priority_versions.add(vk)
                for vk in result.get("version_keys", set()):
                    state.setdefault("downloaded_versions", {})[vk] = group
                save_state(state)
            except (FloodWaitError, PeerFloodError):
                log.error("风控触发，提前结束扫描"); break
            except Exception as e:
                log.error(f"扫描{group}异常: {e}")

        # ---- 并发下载阶段 ----
        all_pending = []
        for result in all_results:
            all_pending.extend(result.get("pending", []))

        if all_pending:
            log.info(f"开始并发下载 {len(all_pending)} 个 IPA（最多 {MAX_CONCURRENT_DOWNLOADS} 并发，单文件超时 {DOWNLOAD_TIMEOUT}秒）")
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
            stop_event = asyncio.Event()
            download_tasks = [
                asyncio.create_task(download_one(client, item, state, semaphore, stop_event))
                for item in all_pending
            ]
            download_results = await asyncio.gather(*download_tasks, return_exceptions=True)

            # 处理下载结果，回填到各群的 result
            for i, dr in enumerate(download_results):
                item = all_pending[i]
                group_result = next((r for r in all_results if r["group"] == item["group_link"]), None)
                if group_result is None:
                    continue

                if isinstance(dr, Exception):
                    # FloodWait / PeerFlood（stop_event 已设置）
                    group_result["errors"].append(f"{item['filename']}: 风控停机")
                elif isinstance(dr, dict):
                    status = dr["status"]
                    if status == "ok":
                        group_result["downloaded"].append({
                            "app": item["app_name"], "filename": item["filename"],
                            "size_mb": round(item["size"]/1024/1024, 1),
                            "ver_key": item["ver_key"],
                        })
                        group_result["version_keys"].add(item["ver_key"])
                    elif status == "timeout":
                        group_result["errors"].append(f"{item['filename']}: 下载超时")
                    elif status == "error":
                        group_result["errors"].append(f"{item['filename']}: {dr.get('error', '未知错误')}")
                    # skipped / cancelled: 不记录
            save_state(state)
        else:
            log.info("本次扫描无需下载新 IPA")

        total_dl = sum(len(r["downloaded"]) for r in all_results)
        total_skipped = sum(r["skipped"] for r in all_results)
        total_ipa = sum(r["ipa_found"] for r in all_results)
        log.info(f"📊 总结: 发现{total_ipa}个IPA, 下载{total_dl}个, 跳过{total_skipped}个")
        await send_scan_notification(config, all_results, total_ipa, total_dl, total_skipped)

        state["last_scan"] = datetime.now().isoformat()
        save_state(state)

    finally:
        try:
            await client.disconnect()
        except Exception as e:
            log.warning(f"断开连接时异常（不影响扫描结果）: {e!r}")
        log.info("断开连接，扫描完成")


if __name__ == "__main__":
    asyncio.run(main())
