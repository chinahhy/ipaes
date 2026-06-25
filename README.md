# IPAes

> 自托管 iOS IPA 私有源，单镜像部署，支持 Esign/AltStore 订阅。

## ✨ 特性

- 🎯 **单镜像单容器** — 类似 Emby，一份 docker-compose 跑全部功能
- 📦 **AltStore/Esign 兼容** — 自动生成标准 repo.json
- 🤖 **TG 自动下载** — 监听指定 Telegram 群，按白名单下载 IPA
- 🔔 **实时刷新** — inotify 监听 IPA 目录，5 秒内入源
- 🖼️ **自动提取图标** — 从 IPA 解析 Info.plist 取最大尺寸图标
- 📊 **同 App 自动去重** — 多版本只显示最新
- 🖥️ **WebUI 管理** — 浏览 APP、管理规则、查看日志
- 🔑 **可热更换鉴权码** — WebUI 一键随机生成订阅地址末段，nginx 自动 reload
- 📝 **破解点说明** — 自动从 TG 消息提取「已解锁/去广告/支持深色」等要点写入订阅源，可在 WebUI 手动覆写

## 🚀 快速开始

### 1. 部署

```bash
mkdir -p ipaes && cd ipaes
curl -O https://raw.githubusercontent.com/chinahhy/ipaes/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/chinahhy/ipaes/main/.env.example
cp .env.example .env
```

### 2. 配置环境变量

编辑 `.env`，至少填写必填项：

| 变量 | 必填 | 说明 |
|------|------|------|
| `REPO_BASE_URL` | ✅ | 源的公网 URL（决定 nginx 路径 + 下载链接前缀） |
| `REPO_NAME` | 否 | Esign 里显示的源名称（默认 `Private IPA Repo`） |
| `REPO_IDENTIFIER` | 否 | 源的 BundleId（默认 `com.private.ipa.repo`） |
| `TG_PROXY` | 否 | TG 代理，留空直连（格式 `socks5://host:port` 或 `http://host:port`） |
| `TG_SCAN_CRON` | 否 | TG 扫描 cron 表达式（默认 `0 1 * * *`，每天 01:00） |
| `TG_SCAN_HOURS` | 否 | 回溯小时数（默认 25） |
| `TZ` | 否 | 时区（默认 `Asia/Shanghai`） |

如需自定义宿主机端口或 IPA/图标目录路径：

| 变量 | 否 | 默认值 |
|------|------|--------|
| `HOST_PORT_NGINX` | 否 | 8080 |
| `HOST_PORT_WEBUI` | 否 | 8085 |
| `IPA_DIR` | 否 | ./data/ipa |
| `ICONS_DIR` | 否 | ./data/icons |

### 3. 启动

```bash
docker compose up -d
```

首次启动会自动生成 `./config/config.json` 模板，填入：
- `api_id` / `api_hash` — 从 https://my.telegram.org 申请
- `phone` — Telegram 注册手机号
- `groups` — 要扫描的群链接
- `whitelist` — 白名单 App 关键词

### 4. TG 首次登录

```bash
docker exec -it ipaes /app/tg-login.sh
```

### 5. 订阅源

把 IPA 文件放入 `${IPA_DIR}`（默认 `./data/ipa/`），5 秒后自动入源。订阅 URL 即 `REPO_BASE_URL`。

## 📁 目录结构

```
ipaes/
├── docker-compose.yml    # 部署编排
├── .env.example          # 环境变量模板
├── .env                  # 真实配置（不进仓库）
├── config/               # TG 配置（首次启动自动生成）
├── data/
│   ├── ipa/              # IPA 文件
│   ├── icons/            # 自动提取的图标
│   └── repo.json         # 自动生成的源
├── session/              # TG 登录态
└── logs/                 # 所有日志
```

## 🌐 反代说明

默认暴露两个端口：
- `8080` → nginx 源订阅入口（Esign/AltStore 访问）
- `8085` → WebUI 管理界面

**WebUI 可选不公开**：如果不希望 WebUI 对外暴露，可在 `.env` 中设置 `HOST_PORT_WEBUI=` 留空，或仅绑定 `127.0.0.1:8085:8085`。

如果需要公网访问，推荐使用反向代理：

**Cloudflare Tunnel（推荐）**：
```bash
docker run -d --name cloudflared-ipa --restart unless-stopped \
  cloudflare/cloudflared:latest tunnel --no-autoupdate run --token <YOUR_TUNNEL_TOKEN>
```

将 Tunnel 指向 `http://ipaes:80` 即可。

**Nginx 反代**：
```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

## 🛠️ 维护

```bash
# 升级
docker compose pull && docker compose up -d

# 手动触发 TG 扫描
docker exec ipaes /app/run-tg-scan.sh

# 手动重建 repo.json
docker exec ipaes /app/scanner.py

# 查看日志
docker compose logs -f
tail -f logs/scanner.log
tail -f logs/tg-cron.log
```

## 📜 License

MIT
