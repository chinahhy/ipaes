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

# === 3. 写入 cron 任务（占位，最终值在 4.5 解锁码逻辑之后重写）===
# 这里先生成框架；4.5 重写 REPO_BASE_URL 后会重写一遍 cron 文件
write_cron_file() {
cat > /etc/cron.d/ipa-self-host <<EOF
# IPA Self-Host TG 自动扫描
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
REPO_BASE_URL=$REPO_BASE_URL
TG_PROXY=$TG_PROXY
TG_SCAN_HOURS=$TG_SCAN_HOURS
REPO_NAME=$REPO_NAME
REPO_IDENTIFIER=$REPO_IDENTIFIER

$TG_SCAN_CRON root /app/run-tg-scan.sh
EOF
chmod 0644 /etc/cron.d/ipa-self-host
}
write_cron_file
echo "✅ Cron 任务已写入 /etc/cron.d/ipa-self-host"


# === 4. 准备日志目录 ===
mkdir -p /logs /var/log/nginx
touch /logs/nginx-access.log /logs/nginx-error.log /logs/scanner.log /logs/tg-cron.log /logs/tg-runtime.log

# === 4.5 解锁码（优先 /config/unlock.json，回退 REPO_BASE_URL 末段）===
# 初始化 unlock.json（不存在则用默认 142536）
if [ ! -f /config/unlock.json ]; then
    echo '{"enabled": true, "code": "142536"}' > /config/unlock.json
fi
UNLOCK_ENABLED=$(python3 -c "import json;print(json.load(open('/config/unlock.json')).get('enabled',True))" 2>/dev/null || echo "True")
UNLOCK_CODE=$(python3 -c "import json;print(json.load(open('/config/unlock.json')).get('code','142536'))" 2>/dev/null || echo "142536")

if [ "$UNLOCK_ENABLED" = "True" ] && [ -n "$UNLOCK_CODE" ]; then
    REPO_PATH="$UNLOCK_CODE"
    echo "🔐 解锁码已启用: /$REPO_PATH"
else
    # 兼容旧逻辑：从 REPO_BASE_URL 提取末段；否则用 repo
    REPO_PATH=$(echo "$REPO_BASE_URL" | sed -E 's|^https?://[^/]+/?||; s|/$||' | awk -F/ '{print $NF}')
    [ -z "$REPO_PATH" ] && REPO_PATH="repo"
    echo "🔓 解锁码已禁用，路径: /$REPO_PATH"
fi
# 同步 REPO_BASE_URL 末段（让 scanner.py 生成正确的 downloadURL）
_BASE_HOST=$(echo "$REPO_BASE_URL" | sed -E 's|(https?://[^/]+).*|\1|')
export REPO_BASE_URL="$_BASE_HOST/$REPO_PATH"
echo "🔗 Nginx 入口路径: /$REPO_PATH"
echo "📦 重写后的 REPO_BASE_URL: $REPO_BASE_URL"

# 用最终 REPO_BASE_URL 重写 cron（让定时扫描的 scanner 用对的 URL）
write_cron_file
echo "✅ Cron 已用最终 REPO_BASE_URL 重写"

cat > /etc/nginx/conf.d/server.conf <<NGX_EOF
server {
    listen 80 default_server;
    server_name _;
    charset utf-8;

    add_header Access-Control-Allow-Origin * always;
    add_header Access-Control-Allow-Methods "GET, HEAD, OPTIONS" always;

    # 健康检查
    location = /healthz {
        access_log off;
        return 200 "ok\n";
    }

    # 订阅入口：无扩展名URL返回 repo.json（Esign要求）
    location = /$REPO_PATH {
        default_type application/json;
        alias /data/repo.json;
    }

    # IPA 下载
    location ^~ /$REPO_PATH/ipa/ {
        alias /data/ipa/;
        autoindex on;
        autoindex_exact_size off;
        autoindex_localtime on;
    }

    # 图标下载
    location ^~ /$REPO_PATH/icons/ {
        alias /data/icons/;
        autoindex on;
        autoindex_exact_size off;
        autoindex_localtime on;
    }

    # WebUI 内部下载代理（让 webui 的"下载"按钮即使不知道解锁码也能下）
    # 端口 8085 走 Basic Auth 鉴权，内部 proxy 不再额外验证
    location ^~ /_ipa_proxy/ {
        alias /data/ipa/;
        add_header Content-Disposition "attachment" always;
    }

    # 默认根路径屏蔽
    location = / { return 404; }
    location / { return 404; }
}
NGX_EOF

echo "✅ Nginx server 配置已生成"


# === 5. 首次启动扫一次 IPA 源（保证 repo.json 立刻存在）===
/app/scanner.py 2>&1 | tee -a /logs/scanner.log || echo "首次扫描失败（可能 ipa 目录为空）"

# === 6. 启动 supervisord ===
echo "🚀 启动 supervisord..."
exec "$@"
