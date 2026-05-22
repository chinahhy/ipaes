#!/bin/bash
# IPA Self-Host 容器启动入口
# - 初始化配置（如不存在则复制模板）
# - 写入 cron 任务（根据环境变量 TG_SCAN_CRON）
# - 启动 supervisord 接管 nginx + scanner + cron
set -e

echo "================================================"
echo "  IPA Self-Host  (chinahhy/ipa-self-host)"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================"

# === 1. 初始化 config 目录 ===
if [ ! -f /config/config.json ]; then
    echo "⚠️  /config/config.json 不存在，从模板复制"
    cp /config.example/config.json /config/config.json
    echo "📝 请编辑 /config/config.json 填入 TG api_id/api_hash 等"
fi

# === 2. 设置基础环境变量默认值 ===
export REPO_BASE_URL="${REPO_BASE_URL:-https://example.com/repo}"
export TG_PROXY="${TG_PROXY:-}"
export TG_SCAN_CRON="${TG_SCAN_CRON:-0 1 * * *}"
export TG_SCAN_HOURS="${TG_SCAN_HOURS:-25}"
export REPO_NAME="${REPO_NAME:-Private IPA Repo}"
export REPO_IDENTIFIER="${REPO_IDENTIFIER:-com.private.ipa.repo}"

echo "📦 Repo URL: $REPO_BASE_URL"
echo "⏰ TG 扫描 Cron: $TG_SCAN_CRON (每次回溯 ${TG_SCAN_HOURS}h)"
[ -n "$TG_PROXY" ] && echo "🌐 TG 代理: $TG_PROXY"

# === 3. 写入 cron 任务 ===
# Debian cron 用 /etc/cron.d/ 目录
cat > /etc/cron.d/ipa-self-host <<EOF
# IPA Self-Host TG 自动扫描
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
REPO_BASE_URL=$REPO_BASE_URL
TG_PROXY=$TG_PROXY
TG_SCAN_HOURS=$TG_SCAN_HOURS
REPO_NAME=$REPO_NAME
REPO_IDENTIFIER=$REPO_IDENTIFIER

$TG_SCAN_CRON root /app/run-tg-scan.sh >> /logs/tg-cron.log 2>&1
EOF
chmod 0644 /etc/cron.d/ipa-self-host
echo "✅ Cron 任务已写入 /etc/cron.d/ipa-self-host"

# === 4. 准备日志目录 ===
mkdir -p /logs /var/log/nginx
touch /logs/nginx-access.log /logs/nginx-error.log /logs/scanner.log /logs/tg-cron.log

# === 5. 首次启动扫一次 IPA 源（保证 repo.json 立刻存在）===
/app/scanner.py 2>&1 | tee -a /logs/scanner.log || echo "首次扫描失败（可能 ipa 目录为空）"

# === 6. 启动 supervisord ===
echo "🚀 启动 supervisord..."
exec "$@"
