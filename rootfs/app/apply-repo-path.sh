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
#   1. 重写 /etc/cron.d/ipa-self-host
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
echo "🔗 [apply-repo-path] REPO_PATH=${REPO_PATH:-<root>}"
echo "📦 [apply-repo-path] FINAL_URL=$FINAL_URL"

# === 写 cron ===
cat > /etc/cron.d/ipa-self-host <<EOF
# IPA Self-Host TG 自动扫描
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
REPO_BASE_URL=$REPO_BASE_URL
IPA_ACCESS_TOKEN=${IPA_ACCESS_TOKEN:-}
TG_PROXY=${TG_PROXY:-}
TG_SCAN_HOURS=${TG_SCAN_HOURS:-25}
REPO_NAME=${REPO_NAME:-Private IPA Repo}
REPO_IDENTIFIER=${REPO_IDENTIFIER:-com.private.ipa.repo}

${TG_SCAN_CRON:-0 1 * * *} root /app/run-tg-scan.sh
EOF
chmod 0644 /etc/cron.d/ipa-self-host

# === 写 nginx server.conf ===
if [ -z "$REPO_PATH" ]; then
    SUB_LOCATION='location = / {'
    SUB_BODY='default_type application/json; root /data; rewrite ^ /repo.json break;'
    IPA_LOCATION='location ^~ /ipa/ {'
    ICONS_LOCATION='location ^~ /icons/ {'
    AUTH_LOCATION='location = /auth {'
    DEFAULT_LOCATION='location / { return 404; }'
else
    SUB_LOCATION="location = /$REPO_PATH {"
    SUB_BODY='default_type application/json; alias /data/repo.json;'
    IPA_LOCATION="location ^~ /$REPO_PATH/ipa/ {"
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

# 把 FINAL_URL 写回给调用方读
echo "$FINAL_URL" > /tmp/repo_base_url.applied
