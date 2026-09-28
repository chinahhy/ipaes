"""Serialize processes using the shared Telethon SQLite session."""

import fcntl
from pathlib import Path


LOCK_PATH = Path("/session/tg-ipa-bot.lock")


class SessionBusy(RuntimeError):
    pass


def acquire_session_lock(path=LOCK_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise SessionBusy("Telegram session 正被扫描或转发任务使用，请稍后再试") from exc
    return handle
