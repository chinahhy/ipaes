#!/usr/bin/env python3
"""非交互式登录后端，由 tg-login.sh 调用"""
import asyncio, json, sys, os
from pathlib import Path
from urllib.parse import urlparse
from telethon import TelegramClient

CONFIG_PATH = Path("/config/config.json")
SESSION_PATH = Path("/session/tg-ipa-bot")
PHONE_CODE_PATH = Path("/config/phone_code_hash.json")

def parse_proxy(s):
    if not s: return None
    p = urlparse(s)
    return ((p.scheme or "socks5").lower(), p.hostname, p.port or 1080)

PROXY = parse_proxy(os.environ.get("TG_PROXY", ""))

async def main():
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)
    phone = config.get("phone")
    if not phone:
        print("ERROR: config.json 缺少 phone 字段"); sys.exit(1)

    client = TelegramClient(
        str(SESSION_PATH), config["api_id"], config["api_hash"],
        device_model="ipa-self-host", system_version="1.0",
        proxy=PROXY,
    )
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"ALREADY_LOGGED_IN: {me.first_name} (@{me.username or 'N/A'})")
        await client.disconnect(); return

    if os.environ.get("SEND_CODE") == "1":
        print(f"SENDING_CODE to {phone}...")
        sent = await client.send_code_request(phone)
        with open(PHONE_CODE_PATH, "w") as f:
            json.dump({"phone_code_hash": sent.phone_code_hash}, f)
        print(f"CODE_SENT: 验证码已发送到 {phone}")
        await client.disconnect(); return

    code = os.environ.get("LOGIN_CODE")
    if not code:
        print("ERROR: 请设置 LOGIN_CODE 环境变量"); sys.exit(1)

    with open(PHONE_CODE_PATH, "r") as f:
        hash_data = json.load(f)

    try:
        await client.sign_in(phone, code, phone_code_hash=hash_data["phone_code_hash"])
    except Exception as e:
        err = str(e)
        if "password" in err.lower() or "Two-step" in err:
            pwd = os.environ.get("TG_2FA_PASSWORD")
            if not pwd:
                print("NEED_2FA: 请设置 TG_2FA_PASSWORD 环境变量"); sys.exit(3)
            await client.sign_in(password=pwd)
        else:
            print(f"LOGIN_FAILED: {e}"); sys.exit(4)

    me = await client.get_me()
    print(f"LOGIN_SUCCESS: {me.first_name} (@{me.username or 'N/A'})")
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
