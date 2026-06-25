#!/bin/bash
# 首次登录脚本：发送验证码 → 输入验证码 → 完成登录
# 用法: docker exec -it ipaes /app/tg-login.sh
set -e

if [ -f /session/tg-ipa-bot.session ]; then
    echo "⚠️  Session 文件已存在，检查授权状态..."
    /usr/bin/python3 /app/tg_login.py
    exit 0
fi

echo "================================================"
echo "  Telegram 首次登录"
echo "================================================"
echo ""
echo "请确认 /config/config.json 已填入正确的 api_id/api_hash/phone"
echo "按回车继续，Ctrl+C 取消..."
read

# Step 1: 发送验证码
SEND_CODE=1 /usr/bin/python3 /app/tg_login.py

echo ""
read -p "请输入收到的验证码: " CODE

# Step 2: 用验证码登录
LOGIN_CODE="$CODE" /usr/bin/python3 /app/tg_login.py

# 设置权限
chmod 600 /session/tg-ipa-bot.session 2>/dev/null || true
echo ""
echo "✅ 登录完成！"
