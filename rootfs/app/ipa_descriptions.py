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
MIN_HIGHLIGHTS = 3

# 命中"破解点"的关键词。命中其中任何一个就把这一行视为高亮。
# 注意：这里是"内容关键词"，跟下面 SKIP 的"段落标题/导引词"不同。
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

# 段落标题：这些行本身只是分组标题，不算破解点
SECTION_HEADER_RE = re.compile(
    r"^(?:[\W_]*)(?:破解内容|破解说明|破解点|安装方法|安装说明|安装教程|"
    r"使用方法|使用说明|更新内容|更新日志|更新说明|版本说明|"
    r"温馨提示|提示|注意事项|声明|免责声明|警告|"
    r"资源频道|讨论频道|购买证书|交流群|频道地址|投稿|商务|联系)"
    r"(?:[:：]?\s*)$"
)

# 整行直接跳过：链接、tag、订阅广告、安装/分发指引等（针对原始 raw 行）。
SKIP_PATTERNS = (
    re.compile(r"^\s*$"),
    re.compile(r"^https?://", re.IGNORECASE),
    re.compile(r"^#\S"),
    re.compile(r"^@\w"),
    re.compile(r"^t\.me/", re.IGNORECASE),
    re.compile(r"^[【\[]?(频道|分享|投稿|交流|订阅|联系|广告|推广|商务)", re.IGNORECASE),
    # markdown 加粗的 hashtag 行：**#xxx**** ****#yyy****
    re.compile(r"^\s*\*+#\S"),
)

# 针对 markdown 清洗后的行：整行只剩若干 `#tag`（用空格分隔）就跳过
TAG_ONLY_LINE_RE = re.compile(r"^\s*#\S+(?:\s+#\S+)*\s*$")

# 包含即跳过：常见安装 / 分发 / 自我宣传话术，不属于破解点。
# 这里只放"复合短语"，避免误伤合法破解点（"下载视频"/"截屏下载"等）。
SKIP_CONTAINS = (
    # 安装 / 分发指引
    "巨魔", "证书签名", "证书安装", "证书登陆", "证书登录", "自签教程", "签名安装",
    "购买证书", "appds.vip", "iOS用户需", "需自签", "请使用",
    # 频道 / 自我宣传
    "资源频道", "讨论频道", "讨论群", "交流群", "频道地址",
    # 反馈话术
    "如遇bug", "请截图", "请添加微信", "永久订阅",
    "由群友", "提供方法", "提供教程",
    # 安卓商店分流提示
    "应用商店", "华为应用", "vivo应用", "oppo应用",
)

# Telegram Markdown 残片清理：在落到候选行之前先把 markdown 标记拆掉，
# 避免出现 `**#xxx****`、`****破解内容`、`📄**[**文本**](url)**` 这种噪声。
_MD_LINK_RE = re.compile(r"\[(?P<text>[^\[\]\n]+?)\]\(\s*[^)\s]+?\s*\)")
_MD_INLINE_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_MD_BOLD_STAR_RE = re.compile(r"\*+")
_MD_BOLD_UND_RE = re.compile(r"__+")
_HEAD_PUNCT_RE = re.compile(r"^[\s•·\-—\*\+◆◇■□▪▫●○★☆※→➤➜👉🟢🟡🔴🟠🟣🟤⚪⚫🆕🚨📢📌📎📄📱✅✈️❌🍡🌸🌙🍵🌟❗❕]+")
_TAIL_PUNCT_RE = re.compile(r"[\s，。、；：:;,\.!?！？·•—\-]+$")


def _strip_markdown(s: str) -> str:
    if not s:
        return ""
    # [文本](url) → 文本
    s = _MD_LINK_RE.sub(lambda m: m.group("text"), s)
    # 裸链接直接去掉
    s = _MD_INLINE_URL_RE.sub("", s)
    # **/__ 加粗下划线全部去掉（含 ****/__**__ 这种噪声）
    s = _MD_BOLD_STAR_RE.sub("", s)
    s = _MD_BOLD_UND_RE.sub("", s)
    return s


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
    s = _strip_markdown(line or "").strip()
    # 去掉行首项目符号 / emoji 装饰
    s = _HEAD_PUNCT_RE.sub("", s).strip()
    # 折叠多空格
    s = re.sub(r"\s{2,}", " ", s).strip()
    # 去掉行尾标点
    s = _TAIL_PUNCT_RE.sub("", s).strip()
    if len(s) > MAX_HIGHLIGHT_LEN:
        s = s[: MAX_HIGHLIGHT_LEN - 1] + "…"
    return s


def _looks_like_install_or_promo(line: str) -> bool:
    """命中安装指引、自我宣传、求助/反馈话术等"非破解点"行。"""
    return any(token in line for token in SKIP_CONTAINS)


def extract_highlights(text: str) -> list[str]:
    """从 TG 消息文本中提取"破解点"高亮列表。

    流程：
      1. 整段先 strip Telegram Markdown，避免按行扫时被 ** / [..](..)
         这种格式裂开；
      2. 按行扫描，跳过空行 / 链接 / tag / 段落标题 / 安装指引 / 自我宣传；
      3. 命中 HIGHLIGHT_KEYWORDS 的行进 highlights，不命中暂存 fallback；
      4. highlights 不足 MIN_HIGHLIGHTS 时再从 fallback 补到目标条数；
         都没有就返回 []。
    """
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
        if TAG_ONLY_LINE_RE.match(cleaned):
            continue
        if SECTION_HEADER_RE.match(cleaned):
            continue
        if _looks_like_install_or_promo(cleaned):
            continue
        seen.add(cleaned)
        if any(kw in cleaned for kw in HIGHLIGHT_KEYWORDS):
            highlights.append(cleaned)
            if len(highlights) >= MAX_HIGHLIGHTS:
                break
        elif len(fallback) < MAX_HIGHLIGHTS:
            fallback.append(cleaned)

    # highlights 数量少时用 fallback 补一些"普通正文行"（功能说明/版本细节）
    if len(highlights) < MIN_HIGHLIGHTS:
        for line in fallback:
            if line in highlights:
                continue
            highlights.append(line)
            if len(highlights) >= MIN_HIGHLIGHTS:
                break
    return highlights[:MAX_HIGHLIGHTS]


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
