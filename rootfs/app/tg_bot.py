#!/usr/bin/env python3
"""
Telegram IPA Scanner - cron 触发，按白名单下载IPA
配置全部来自 /config/config.json + 环境变量

环境变量:
  TG_PROXY      - socks5://host:port，可空（直连）
  TG_SCAN_HOURS - 回溯小时数（默认25）
"""
import asyncio, json, logging, os, random, re, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.errors import FloodWaitError, PeerFloodError
from telethon.tl.types import DocumentAttributeFilename

CONFIG_PATH = Path("/config/config.json")
SESSION_PATH = Path("/session/tg-ipa-bot")
LOG_PATH = Path("/logs/scanner.log")
DOWNLOAD_DIR = Path("/data/ipa")
STATE_PATH = Path("/config/state.json")

# 解析代理环境变量 socks5://host:port → ("socks5", host, port)
def parse_proxy(proxy_url: str):
    if not proxy_url: return None
    p = urlparse(proxy_url)
    scheme = (p.scheme or "socks5").lower()
    return (scheme, p.hostname, p.port or 1080)

PROXY = parse_proxy(os.environ.get("TG_PROXY", ""))
SCAN_HOURS = int(os.environ.get("TG_SCAN_HOURS", "25"))

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("tg-bot")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

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

    async for message in client.iter_messages(entity, offset_date=None, reverse=False, limit=500):
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
        if unique_key in downloaded_set:
            log.info(f"  跳过（已下载）: {filename}"); continue

        ver_key = extract_version_key(filename)
        if not is_priority and ver_key in priority_versions:
            log.info(f"  跳过（优先群已有同版本）: {filename}")
            result["skipped"] += 1; continue

        if today_count >= rate_limit["max_per_day"]:
            log.warning(f"  已达今日上限{rate_limit['max_per_day']}"); break

        save_path = DOWNLOAD_DIR / safe_filename(filename)
        log.info(f"  下载: [{app_name}] {filename} ({size/1024/1024:.1f}MB)")
        try:
            await client.download_media(message, file=str(save_path))
            downloaded_set.add(unique_key)
            state["downloaded_files"].append(unique_key)
            today_count += 1
            result["downloaded"].append({
                "app": app_name, "filename": filename,
                "size_mb": round(size/1024/1024, 1), "ver_key": ver_key,
            })
            result["version_keys"].add(ver_key)
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

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    log.info("=" * 60)
    log.info(f"TG扫描开始，回溯{SCAN_HOURS}小时")
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

        state["last_scan"] = datetime.now().isoformat()
        save_state(state)

    finally:
        await client.disconnect()
        log.info("断开连接，扫描完成")


if __name__ == "__main__":
    asyncio.run(main())
