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

# === 4. 准备日志目录 ===
mkdir -p /logs /var/log/nginx
touch /logs/nginx-access.log /logs/nginx-error.log /logs/scanner.log /logs/tg-cron.log /logs/tg-runtime.log

# === 4.5 解锁码（优先 /config/unlock.json，回退 REPO_BASE_URL 末段）===
# 初始化 unlock.json（不存在则保留 REPO_BASE_URL 现有末段，避免破坏 Esign 已订阅 URL；只有完全无可推断时才用默认 142536）
if [ ! -f /config/unlock.json ]; then
    _INIT_CODE=$(echo "$REPO_BASE_URL" | sed -E 's|^https?://[^/]+/?||; s|/$||' | awk -F/ '{print $NF}')
    [ -z "$_INIT_CODE" ] && _INIT_CODE="142536"
    # 用 printf 避免 echo 转义陷阱；jq 不一定有，手写 JSON
    printf '{"enabled": true, "code": "%s"}\n' "$_INIT_CODE" > /config/unlock.json
    echo "📝 初始化 unlock.json，沿用 REPO_BASE_URL 末段: $_INIT_CODE"
fi
UNLOCK_ENABLED=$(python3 -c "import json;print(json.load(open('/config/unlock.json')).get('enabled',True))" 2>/dev/null || echo "True")
UNLOCK_CODE=$(python3 -c "import json;print(json.load(open('/config/unlock.json')).get('code','142536'))" 2>/dev/null || echo "142536")
ACCESS_TOKEN=$(python3 - <<'PY_TOKEN'
import json, secrets
from pathlib import Path
p = Path('/config/unlock.json')
try:
    d = json.loads(p.read_text()) if p.exists() else {}
except Exception:
    d = {}
tok = str(d.get('token') or '').strip()
if not tok:
    tok = secrets.token_urlsafe(24)
    d['token'] = tok
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2))
print(tok)
PY_TOKEN
)
export IPA_ACCESS_TOKEN="$ACCESS_TOKEN"
echo "🔐 访问 token 已启用（值不输出）"

# === 5. 用统一脚本生成 cron / nginx，REPO_PATH 支持 /config/repo_path.json 热更新 ===
chmod +x /app/apply-repo-path.sh 2>/dev/null || true
/app/apply-repo-path.sh
# apply-repo-path.sh 会把最终 URL 写入 /tmp/repo_base_url.applied
if [ -s /tmp/repo_base_url.applied ]; then
    export REPO_BASE_URL="$(cat /tmp/repo_base_url.applied)"
fi
echo "📦 最终 REPO_BASE_URL: $REPO_BASE_URL"
if [ "$UNLOCK_ENABLED" = "True" ] && [ -n "$UNLOCK_CODE" ]; then
    echo "🔐 解锁码已启用（仅 /auth 校验，不写入 URL）"
else
    echo "🔓 解锁码已禁用"
fi

echo "✅ Nginx server 配置已生成"


# === 5. 首次启动扫一次 IPA 源（保证 repo.json 立刻存在）===
/app/scanner.py 2>&1 | tee -a /logs/scanner.log || echo "首次扫描失败（可能 ipa 目录为空）"

# === 6. 启动 supervisord ===
echo "🚀 启动 supervisord..."
exec "$@"
