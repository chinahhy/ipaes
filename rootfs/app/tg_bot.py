#!/usr/bin/env python3
"""
Telegram IPA Scanner - cron 触发，按白名单下载IPA
配置全部来自 /config/config.json + 环境变量

环境变量:
  TG_PROXY      - socks5://host:port，可空（直连）
  TG_SCAN_HOURS - 回溯小时数（默认25）
  TG_SCAN_LIMIT - 每个群最多读取消息数（默认500）
"""
import asyncio, json, logging, os, random, re, sys, zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

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

# 解析代理环境变量 socks5://host:port → ("socks5", host, port)
def parse_proxy(proxy_url: str):
    if not proxy_url: return None
    p = urlparse(proxy_url)
    scheme = (p.scheme or "socks5").lower()
    return (scheme, p.hostname, p.port or 1080)

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

PROXY = parse_proxy(load_proxy_url())
SCAN_HOURS = int(os.environ.get("TG_SCAN_HOURS", "25"))
SCAN_LIMIT = int(os.environ.get("TG_SCAN_LIMIT", "500"))

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

def build_notification_text(downloaded, total_ipa, total_dl, total_skipped, errors_count):
    lines = [
        "🌸 IPA 入库扫描完成啦～",
        "おつかれさまです，今天也辛苦啦 ʕっ•ᴥ•ʔっ ✨",
        "",
        f"🎀 本次新入库 {total_dl} 个版本：",
    ]
    for item in downloaded[:12]:
        lines.append(f"🍡 {item['app']} · {version_label(item['filename'])}")
        lines.append(f"   {item['filename']} · {item['size_mb']}MB")
    if len(downloaded) > 12:
        lines.append(f"…还有 {len(downloaded) - 12} 个新版本，去 WebUI 看完整列表喔～")
    lines.extend([
        "",
        f"📊 扫描小结：发现 {total_ipa} 个 IPA / 入库 {total_dl} 个 / 跳过 {total_skipped} 个",
    ])
    if errors_count:
        lines.append(f"🍵 有 {errors_count} 个小问题，详情在日志里。")
    lines.append("きらきら～仓库已经更新好啦 🌙")
    return "\n".join(lines)

async def send_scan_notification(config, all_results, total_ipa, total_dl, total_skipped):
    downloaded = [item for result in all_results for item in result.get("downloaded", [])]
    log.info(f"通知准备: total_ipa={total_ipa}, total_dl={total_dl}, downloaded_count={len(downloaded)}")
    if not downloaded:
        log.info("Bot 通知跳过：本次无新增入库")
        return
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
    errors_count = sum(len(result.get("errors", [])) for result in all_results)
    text = build_notification_text(downloaded, total_ipa, total_dl, total_skipped, errors_count)
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
    result = {"group": group_link, "total_msgs": 0, "ipa_found": 0,
              "downloaded": [], "skipped": 0, "errors": [], "version_keys": set()}

    try:
        entity = await client.get_entity(group_link)
        group_title = getattr(entity, "title", group_link)
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

    async for message in client.iter_messages(entity, offset_date=None, reverse=False, limit=SCAN_LIMIT):
        if message.date < since: break
        result["total_msgs"] += 1

        filename = get_filename(message)
        if not filename or not filename.lower().endswith(".ipa"): continue
        result["ipa_found"] += 1

        app_name = match_whitelist(filename, message.text or "", whitelist)
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
            log.warning(f"  已达今日上限{rate_limit['max_per_day']}"); break

        if part_path.exists():
            try:
                part_path.unlink()
            except OSError:
                pass

        log.info(f"  下载: [{app_name}] {filename} ({size/1024/1024:.1f}MB)")
        try:
            await client.download_media(message, file=str(part_path))
            ok, reason = validate_ipa(part_path, size)
            if not ok:
                raise RuntimeError(reason)
            part_path.replace(save_path)
            downloaded_set.add(unique_key)
            if unique_key not in state["downloaded_files"]:
                state["downloaded_files"].append(unique_key)
            today_count += 1
            result["downloaded"].append({
                "app": app_name, "filename": filename,
                "size_mb": round(size/1024/1024, 1), "ver_key": ver_key,
            })
            result["version_keys"].add(ver_key)
            # 记录"破解点 / 版本说明"，由 scanner.py 写入 repo.json，WebUI 也会读
            try:
                msg_text = (message.text or message.message or "")
                source_url = ""
                try:
                    source_url = f"{group_link.rstrip('/')}/{message.id}" if group_link.startswith("http") else ""
                except Exception:
                    source_url = ""
                ipa_desc.remember_from_message(save_path.name, msg_text, source_url)
            except Exception as _e:
                log.warning(f"  记录 IPA 描述失败: {_e}")
            log.info(f"  OK: {save_path.name}")
            sleep_sec = random.randint(rate_limit["min_interval_sec"], rate_limit["max_interval_sec"])
            log.info(f"  等待 {sleep_sec}s..."); await asyncio.sleep(sleep_sec)
        except FloodWaitError as e:
            log.error(f"  FloodWait! 需等{e.seconds}s，紧急停机")
            result["errors"].append(f"FloodWait: {e.seconds}s"); raise
        except PeerFloodError as e:
            log.error(f"  PeerFlood! 账号可能被风控")
            result["errors"].append(f"PeerFlood: {e}"); raise
        except Exception as e:
            try:
                part_path.unlink()
            except OSError:
                pass
            log.error(f"  下载失败: {e}")
            result["errors"].append(f"{filename}: {e}")

    return result


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
    log.info(f"代理: {PROXY or '直连'}")

    client = TelegramClient(
        str(SESSION_PATH), api_id, api_hash,
        device_model="ipa-self-host", system_version="1.0",
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
                log.error("风控触发，提前结束"); break
            except Exception as e:
                log.error(f"扫描{group}异常: {e}")

        total_dl = sum(len(r["downloaded"]) for r in all_results)
        total_skipped = sum(r["skipped"] for r in all_results)
        total_ipa = sum(r["ipa_found"] for r in all_results)
        log.info(f"📊 总结: 发现{total_ipa}个IPA, 下载{total_dl}个, 跳过{total_skipped}个")
        await send_scan_notification(config, all_results, total_ipa, total_dl, total_skipped)

        state["last_scan"] = datetime.now().isoformat()
        save_state(state)

    finally:
        await client.disconnect()
        log.info("断开连接，扫描完成")


if __name__ == "__main__":
    asyncio.run(main())
