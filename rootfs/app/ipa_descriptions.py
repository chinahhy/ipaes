#!/usr/bin/env python3
"""IPA 破解点 / 版本说明 数据层。

数据持久化在 /data/.ipa_descriptions.json，按 IPA 文件名做 key：

    {
      "Spotify_8.10.74_xxx.ipa": {
        "highlights": ["已解锁 Premium", "去除广告", "可下载离线歌曲"],
        "raw_text": "<原始 TG 消息正文，截断 1500 字符>",
        "source": "https://t.me/somechannel/12345",
        "saved_at": "2026-06-16T12:34:56",
        "manual": false
      }
    }

- highlights：高亮的"破解点"列表，最多 6 条
- raw_text：TG 消息原文的截断版，便于人工复核
- manual=true：用户在 WebUI 手动编辑过，自动同步逻辑应该跳过覆盖

模块只做 IO + 简单文本提取，不依赖第三方库。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

DESC_PATH = Path("/data/.ipa_descriptions.json")
MAX_RAW = 1500
MAX_HIGHLIGHTS = 6
MAX_HIGHLIGHT_LEN = 80

# 命中"破解点"的关键词。命中其中任何一个就把这一行视为高亮。
HIGHLIGHT_KEYWORDS = (
    "破解", "解锁", "去广告", "无广告", "去除广告", "屏蔽广告",
    "广告", "横幅",
    "会员", "VIP", "vip", "Premium", "premium", "高级版", "Pro 版", "PRO",
    "已登录", "已订阅", "免登录", "免登陆", "免会员",
    "内购", "解除限制", "去验证", "免验证", "免广告",
    "多账号", "多开", "去更新", "去校验", "去签名校验",
    "增强", "美化", "修改", "干净版", "纯净版",
    "兑换", "签到", "礼包", "本地化", "汉化",
    "深色", "新增", "支持", "修复", "优化",
)

# 这些行直接跳过（链接、标签、订阅广告等）
SKIP_PATTERNS = (
    re.compile(r"^\s*$"),
    re.compile(r"^https?://", re.IGNORECASE),
    re.compile(r"^#\S"),
    re.compile(r"^@\w"),
    re.compile(r"^t\.me/", re.IGNORECASE),
    re.compile(r"^[【\[]?(频道|分享|投稿|交流|订阅|联系|广告|推广|商务)", re.IGNORECASE),
)


def _load() -> dict:
    if not DESC_PATH.exists():
        return {}
    try:
        return json.loads(DESC_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    DESC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DESC_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean_line(line: str) -> str:
    s = line.strip().lstrip("•-*·•").strip()
    # 折叠多空格
    s = re.sub(r"\s{2,}", " ", s)
    if len(s) > MAX_HIGHLIGHT_LEN:
        s = s[: MAX_HIGHLIGHT_LEN - 1] + "…"
    return s


def extract_highlights(text: str) -> list[str]:
    """从 TG 消息文本中提取"破解点"高亮列表。

    简单启发式：按行扫描，跳过链接 / 标签 / 订阅广告类，命中 HIGHLIGHT_KEYWORDS
    的行优先；没有命中时退化为前几行有效内容。"""
    if not text:
        return []
    lines = re.split(r"[\r\n]+", text)
    highlights: list[str] = []
    fallback: list[str] = []
    seen: set[str] = set()

    for raw in lines:
        if any(p.search(raw) for p in SKIP_PATTERNS):
            continue
        cleaned = _clean_line(raw)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        if any(kw in cleaned for kw in HIGHLIGHT_KEYWORDS):
            highlights.append(cleaned)
            if len(highlights) >= MAX_HIGHLIGHTS:
                break
        elif len(fallback) < MAX_HIGHLIGHTS:
            fallback.append(cleaned)

    if highlights:
        return highlights
    return fallback[:MAX_HIGHLIGHTS]


def get(filename: str) -> dict:
    data = _load()
    return data.get(filename) or {}


def get_all() -> dict:
    return _load()


def remember_from_message(filename: str, message_text: str, source: str = "") -> dict:
    """在 TG 下载入库时调用。如果用户已经手动编辑过，则不覆盖。"""
    if not filename:
        return {}
    data = _load()
    existing = data.get(filename) or {}
    if existing.get("manual"):
        return existing
    highlights = extract_highlights(message_text or "")
    raw_text = (message_text or "").strip()
    if len(raw_text) > MAX_RAW:
        raw_text = raw_text[: MAX_RAW - 1] + "…"
    record = {
        "highlights": highlights,
        "raw_text": raw_text,
        "source": source or existing.get("source", ""),
        "saved_at": datetime.now().replace(microsecond=0).isoformat(),
        "manual": False,
    }
    data[filename] = record
    _save(data)
    return record


def set_manual(filename: str, highlights: Iterable[str], raw_text: str = "") -> dict:
    if not filename:
        return {}
    cleaned: list[str] = []
    seen: set[str] = set()
    for h in highlights or []:
        s = _clean_line(str(h))
        if not s or s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
        if len(cleaned) >= MAX_HIGHLIGHTS:
            break
    data = _load()
    record = {
        "highlights": cleaned,
        "raw_text": (raw_text or "")[:MAX_RAW],
        "source": (data.get(filename) or {}).get("source", ""),
        "saved_at": datetime.now().replace(microsecond=0).isoformat(),
        "manual": True,
    }
    data[filename] = record
    _save(data)
    return record


def reset(filename: str) -> bool:
    """清掉 manual 标记，让下次 TG 入库重新覆盖。返回是否真的有记录被改。"""
    data = _load()
    if filename not in data:
        return False
    data[filename]["manual"] = False
    _save(data)
    return True


def prune(known_filenames: Iterable[str]) -> int:
    keep = set(known_filenames)
    data = _load()
    removed = [k for k in list(data.keys()) if k not in keep]
    if not removed:
        return 0
    for k in removed:
        data.pop(k, None)
    _save(data)
    return len(removed)
