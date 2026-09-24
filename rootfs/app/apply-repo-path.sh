#!/bin/bash
# 计算最终 REPO_BASE_URL → 写 cron → 写 nginx server.conf。
# 既被 entrypoint.sh 启动时调用，也可以被 WebUI 在用户更换鉴权码后热调用。
#
# 输入：
#   - 环境变量 REPO_BASE_URL（来自 docker-compose .env，固定不变）
#   - 可选覆盖文件 /config/repo_path.json 形如 {"code": "abc123"}
#     若 code 非空，则替换 REPO_BASE_URL 末段为 code。
#
# 副作用：
#   1. 重写 /etc/cron.d/ipaes
#   2. 重写 /etc/nginx/conf.d/server.conf
#   3. 若 nginx master 在跑，nginx -s reload
#   4. 触发一次 scanner.py 重新生成 repo.json
set -e

RAW_URL="${REPO_BASE_URL:-https://example.com/repo}"
_BASE_HOST=$(echo "$RAW_URL" | sed -E 's|(https?://[^/]+).*|\1|')
REPO_PATH=$(echo "$RAW_URL" | sed -E 's|^https?://[^/]+/?||; s|/$||')

OVERRIDE_CODE=""
if [ -f /config/repo_path.json ]; then
    OVERRIDE_CODE=$(/usr/bin/python3 /app/_read_repo_path_override.py 2>/dev/null || true)
fi

if [ -n "$OVERRIDE_CODE" ]; then
    REPO_PATH="$OVERRIDE_CODE"
fi

if [ -z "$REPO_PATH" ]; then
    FINAL_URL="$_BASE_HOST"
else
    FINAL_URL="$_BASE_HOST/$REPO_PATH"
fi

export REPO_BASE_URL="$FINAL_URL"
# scanner 会在每次新进程启动时优先读取这个文件。必须先原子更新它，
# 再 reload nginx / 触发 scanner；否则热更新当次扫描仍可能读取旧路径。
_APPLIED_FILE="/tmp/repo_base_url.applied"
_APPLIED_TMP="${_APPLIED_FILE}.$$"
printf '%s\n' "$FINAL_URL" > "$_APPLIED_TMP"
mv -f "$_APPLIED_TMP" "$_APPLIED_FILE"
echo "🔗 [apply-repo-path] REPO_PATH=${REPO_PATH:-<root>}"
echo "📦 [apply-repo-path] FINAL_URL=$FINAL_URL"

cron_quote() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

CRON_REPO_BASE_URL=$(cron_quote "$REPO_BASE_URL")
CRON_IPA_ACCESS_TOKEN=$(cron_quote "${IPA_ACCESS_TOKEN:-}")
CRON_TG_PROXY=$(cron_quote "${TG_PROXY:-}")
CRON_TG_SCAN_HOURS=$(cron_quote "${TG_SCAN_HOURS:-25}")
CRON_TG_DOWNLOAD_TIMEOUT=$(cron_quote "${TG_DOWNLOAD_TIMEOUT:-3600}")
CRON_TG_MAX_CONCURRENT=$(cron_quote "${TG_MAX_CONCURRENT:-1}")
CRON_REPO_NAME=$(cron_quote "${REPO_NAME:-Private IPA Repo}")
CRON_REPO_IDENTIFIER=$(cron_quote "${REPO_IDENTIFIER:-com.private.ipa.repo}")

# === 写 cron ===
cat > /etc/cron.d/ipaes <<EOF
# IPA Self-Host TG 自动扫描
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
REPO_BASE_URL="$CRON_REPO_BASE_URL"
IPA_ACCESS_TOKEN="$CRON_IPA_ACCESS_TOKEN"
TG_PROXY="$CRON_TG_PROXY"
TG_SCAN_HOURS="$CRON_TG_SCAN_HOURS"
TG_DOWNLOAD_TIMEOUT="$CRON_TG_DOWNLOAD_TIMEOUT"
TG_MAX_CONCURRENT="$CRON_TG_MAX_CONCURRENT"
REPO_NAME="$CRON_REPO_NAME"
REPO_IDENTIFIER="$CRON_REPO_IDENTIFIER"

${TG_SCAN_CRON:-0 1 * * *} root /app/run-tg-scan.sh
EOF
chmod 0644 /etc/cron.d/ipaes

# === 写 nginx server.conf ===
if [ -z "$REPO_PATH" ]; then
    SUB_LOCATION='location = / {'
    SUB_BODY='default_type application/json; root /data; rewrite ^ /repo.json break;'
    IPA_LOCATION='location ^~ /ipa/ {'
    ARCHIVE_LOCATION='location ^~ /ipa/.archive/ {'
    ICONS_LOCATION='location ^~ /icons/ {'
    AUTH_LOCATION='location = /auth {'
    DEFAULT_LOCATION='location / { return 404; }'
else
    SUB_LOCATION="location = /$REPO_PATH {"
    SUB_BODY='default_type application/json; alias /data/repo.json;'
    IPA_LOCATION="location ^~ /$REPO_PATH/ipa/ {"
    ARCHIVE_LOCATION="location ^~ /$REPO_PATH/ipa/.archive/ {"
    ICONS_LOCATION="location ^~ /$REPO_PATH/icons/ {"
    AUTH_LOCATION="location = /$REPO_PATH/auth {"
    # 根路径与未匹配路径都返回 404；只声明一条 location /，
    # 否则 nginx 启动会报 "duplicate location"。
    DEFAULT_LOCATION='location / { return 404; }'
fi

cat > /etc/nginx/conf.d/server.conf <<NGX_EOF
server {
    listen 80 default_server;
    server_name _;
    charset utf-8;

    add_header Access-Control-Allow-Origin * always;
    add_header Access-Control-Allow-Methods "GET, HEAD, POST, OPTIONS" always;

    location = /healthz {
        access_log off;
        return 200 "ok\n";
    }

    $SUB_LOCATION
        $SUB_BODY
    }

    $ARCHIVE_LOCATION
        return 404;
    }

    $IPA_LOCATION
        alias /data/ipa/;
        autoindex off;
    }

    $ICONS_LOCATION
        alias /data/icons/;
        autoindex on;
        autoindex_exact_size off;
        autoindex_localtime on;
        add_header Cache-Control "no-cache, must-revalidate" always;
    }

    $AUTH_LOCATION
        proxy_pass http://127.0.0.1:8085/auth;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location ^~ /_ipa_proxy/.archive/ {
        return 404;
    }

    location ^~ /_ipa_proxy/ {
        alias /data/ipa/;
        add_header Content-Disposition "attachment" always;
    }

    $DEFAULT_LOCATION
}
NGX_EOF

NGINX_RUNNING=0
if [ -f /var/run/nginx.pid ] && kill -0 "$(cat /var/run/nginx.pid)" 2>/dev/null; then
    NGINX_RUNNING=1
fi

# === reload nginx 若已在跑 ===
if [ "$NGINX_RUNNING" = "1" ]; then
    if /usr/sbin/nginx -t -c /etc/nginx/nginx.conf 2>/dev/null; then
        /usr/sbin/nginx -s reload || true
        echo "✅ [apply-repo-path] nginx reload"
    else
        echo "⚠️ [apply-repo-path] nginx -t 失败，未 reload"
    fi
fi

# === 触发 scanner 用新 BASE_URL 重写 repo.json ===
# 仅在 nginx 已经在跑时主动触发；首次启动时 entrypoint 会自己跑一次 scanner，
# 这里跑会重复且把输出吞掉，反而影响首启日志可读性。
if [ "$NGINX_RUNNING" = "1" ] && [ -x /app/scanner.py ]; then
    /app/scanner.py >/dev/null 2>&1 || true
    echo "✅ [apply-repo-path] scanner.py 完成"
fi

# applied URL 已在生成配置和触发 scanner 之前原子写入。
