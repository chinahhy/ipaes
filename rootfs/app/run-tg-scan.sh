#!/bin/bash
# TG 扫描入口（cron 触发）
# 设计原则：只写一份主日志，且每次运行前自动瘦身，避免 Telethon 网络抖动时把 NAS 日志撑爆。
set -e

LOG=/logs/tg-cron.log
RUNTIME_LOG=/logs/tg-runtime.log
MAX_LOG_BYTES=${MAX_LOG_BYTES:-20971520}  # 单个日志超过 20MB 时只保留最后 5MB，足够排查最近一次扫描。
KEEP_LOG_BYTES=${KEEP_LOG_BYTES:-5242880}

shrink_log_if_needed() {
    local file="$1"
    [ -f "$file" ] || return 0
    local size
    size=$(wc -c < "$file" 2>/dev/null || echo 0)
    if [ "$size" -gt "$MAX_LOG_BYTES" ]; then
        tail -c "$KEEP_LOG_BYTES" "$file" > "${file}.tmp" 2>/dev/null || true
        mv "${file}.tmp" "$file"
    fi
}

mkdir -p /logs
touch "$LOG" "$RUNTIME_LOG"
shrink_log_if_needed "$LOG"
shrink_log_if_needed "$RUNTIME_LOG"

echo "" >> "$LOG"
echo "==== TG scan triggered at $(date '+%Y-%m-%d %H:%M:%S') ====" >> "$LOG"

# Session 检查
if [ ! -f /session/tg-ipa-bot.session ]; then
    echo "❌ Session 未登录，跳过本次扫描" >> "$LOG"
    echo "   请运行: docker exec -it ipa-self-host /app/tg-login.sh" >> "$LOG"
    exit 0
fi

# 跑 TG 扫描：
# - tg_bot.py 自己把业务摘要写入 $LOG；这里不再把 stdout 再追加回同一个文件，避免重复写。
# - 第三方库/解释器 stderr 单独进 runtime 日志，避免污染人看的扫描摘要。
TG_LOG_PATH="$LOG" /usr/bin/python3 /app/tg_bot.py >> "$RUNTIME_LOG" 2>&1 || echo "❌ tg_bot.py 异常退出，细节见 $RUNTIME_LOG" >> "$LOG"

shrink_log_if_needed "$LOG"
shrink_log_if_needed "$RUNTIME_LOG"
echo "==== TG scan done at $(date '+%Y-%m-%d %H:%M:%S') ====" >> "$LOG"
