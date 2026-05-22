#!/bin/bash
# TG 扫描入口（cron 触发）
set -e

LOG=/logs/tg-cron.log
echo "" >> $LOG
echo "==== TG scan triggered at $(date '+%Y-%m-%d %H:%M:%S') ====" >> $LOG

# Session 检查
if [ ! -f /session/tg-ipa-bot.session ]; then
    echo "❌ Session 未登录，跳过本次扫描" >> $LOG
    echo "   请运行: docker exec -it ipa-self-host /app/tg-login.sh" >> $LOG
    exit 0
fi

# 跑 TG 扫描
/usr/bin/python3 /app/tg_bot.py >> $LOG 2>&1 || echo "❌ tg_bot.py 异常退出" >> $LOG

echo "==== TG scan done at $(date '+%Y-%m-%d %H:%M:%S') ====" >> $LOG
