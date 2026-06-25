# IPAes

[![GitHub](https://img.shields.io/badge/GitHub-chinahhy/ipaes-blue?logo=github)](https://github.com/chinahhy/ipaes)
[![Docker Pulls](https://img.shields.io/docker/pulls/hoya0803/ipaes?logo=docker)](https://hub.docker.com/r/hoya0803/ipaes)
[![Docker Image Size](https://img.shields.io/docker/image-size/hoya0803/ipaes/latest?logo=docker)](https://hub.docker.com/r/hoya0803/ipaes)

**iOS IPA 自托管私有源 | Esign/AltStore 兼容 | 单容器部署 | TG 自动下载**

把家里 NAS 变成 iOS 私有签名源，配合 Esign / AltStore 订阅安装 IPA。一个容器、一份 compose、零依赖。

---

## ✨ 特性

- 🎯 **单镜像单容器** — 集成 nginx + 文件监听 + TG下载，不再维护三套
- 📦 **AltStore/Esign 兼容** — 自动生成标准 `repo.json`
- 🤖 **Telegram 自动下载** — 监听指定群，按白名单关键词自动下载 IPA
- 🔔 **实时刷新** — inotify 监听 IPA 目录，5 秒内自动入源
- 🖼️ **自动提取图标** — 从 IPA 解析 Info.plist 取最大尺寸 AppIcon
- 📊 **同 App 智能去重** — 多版本仅保留最新版（按版本号比较）
- 🌐 **代理支持** — 内置 socks5 代理，墙内 NAS 也能扫 TG
- ⏰ **内置 cron** — 定时扫描时间通过环境变量配置
- 🔑 **可热更换鉴权码** — WebUI 一键随机生成订阅地址末段，nginx 自动 reload
- 📝 **破解点说明** — 自动从 TG 消息提取破解要点写入订阅源，WebUI 可手动覆写

## 🚀 一分钟部署

```bash
mkdir -p ipaes && cd ipaes
curl -O https://raw.githubusercontent.com/chinahhy/ipaes/main/docker-compose.yml
# 按需修改 docker-compose.yml 里的 REPO_BASE_URL 等环境变量
docker compose up -d
```

## 📋 完整 docker-compose.yml

```yaml
services:
  ipaes:
    image: hoya0803/ipaes:latest
    container_name: ipaes
    restart: unless-stopped
    ports:
      - "8084:80"
    volumes:
      - ./config:/config          # TG api凭证 + 白名单
      - ./session:/session        # TG 登录态
      - ./logs:/logs              # 所有进程日志
      - ./data:/data              # repo.json 自动生成
      - /your/path/to/ipa:/data/ipa        # 你的IPA存放目录
      - /your/path/to/icons:/data/icons    # 自动提取的图标
    environment:
      - TZ=Asia/Shanghai
      - REPO_BASE_URL=https://your.domain.com/yourpath  # 公网订阅URL
      - REPO_NAME=My Private IPA Repo
      - REPO_IDENTIFIER=com.example.ipa.repo
      - TG_PROXY=socks5://proxy.example.com:1080  # 可空，留空表示直连
      - TG_SCAN_CRON=0 1 * * *             # 每天凌晨1点扫描
      - TG_SCAN_HOURS=25                   # 回溯25小时
```

## ⚙️ 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `REPO_BASE_URL` | 源的公网 URL | - (必填) |
| `REPO_NAME` | 源名称（Esign 显示）| `Private IPA Repo` |
| `REPO_IDENTIFIER` | 源 BundleId | `com.private.ipa.repo` |
| `TG_PROXY` | TG 代理（socks5://host:port） | 空（直连） |
| `TG_SCAN_CRON` | TG 扫描 cron 表达式 | `0 1 * * *` |
| `TG_SCAN_HOURS` | 回溯小时数 | `25` |

## 📁 配置文件

容器首次启动会自动生成 `./config/config.json` 模板，编辑填入：

```json
{
  "api_id": 12345678,
  "api_hash": "your_api_hash",
  "phone": "+8613800138000",
  "groups": ["https://t.me/your_group"],
  "priority_groups": ["https://t.me/priority_group"],
  "whitelist": [
    {"name": "App名", "keywords": ["关键词1", "关键词2"]}
  ],
  "rate_limit": {
    "min_interval_sec": 30,
    "max_interval_sec": 120,
    "max_per_day": 50
  }
}
```

## 🔐 Telegram 首次登录

```bash
docker exec -it ipaes /app/tg-login.sh
```

按提示输入验证码完成登录，session 持久化到 `./session/`。

## 🛠️ 常用命令

```bash
# 拉取最新镜像并重启
docker compose pull && docker compose up -d

# 手动触发 TG 扫描（不等 cron）
docker exec ipaes /app/run-tg-scan.sh

# 手动重建 repo.json
docker exec ipaes /app/scanner.py

# 查看实时日志
docker logs -f ipaes
tail -f logs/scanner.log
tail -f logs/tg-cron.log
```

## 🏗️ 架构

```
公网用户/iPhone Esign 订阅
       ↓ HTTPS
   [反向代理 nginx/Lucky/Traefik]
       ↓ HTTP :8084
┌──────────────────────────────┐
│ 容器 ipaes           │
│ ├─ nginx (对外服务)          │
│ ├─ scanner-watcher (inotify) │
│ └─ cron (TG 定时扫描)        │
└──────────────────────────────┘
```

## 🏷️ 镜像 Tags

| Tag | 说明 |
|---|---|
| `latest` | 主分支最新构建（推荐）|
| `main` | 同 latest |
| `sha-xxxxxxx` | 特定 commit 的镜像 |
| `vX.Y.Z` | 语义化版本（如有发布）|

支持架构：`linux/amd64`、`linux/arm64`

## 📜 License

MIT

## 🔗 链接

- **源代码**：https://github.com/chinahhy/ipaes
- **问题反馈**：https://github.com/chinahhy/ipaes/issues
- **Docker Hub**：https://hub.docker.com/r/hoya0803/ipaes
