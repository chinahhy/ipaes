# IPA Self-Host

> 自托管 iOS IPA 私有源，单镜像部署，支持 Esign/AltStore 订阅。

## ✨ 特性

- 🎯 **单镜像单容器** - 类似 Emby，一份 docker-compose 跑全部功能
- 📦 **AltStore/Esign 兼容** - 自动生成标准 repo.json
- 🤖 **TG 自动下载** - 监听指定 Telegram 群，按白名单下载 IPA
- 🔔 **实时刷新** - inotify 监听 IPA 目录，5 秒内入源
- 🖼️ **自动提取图标** - 从 IPA 解析 Info.plist 取最大尺寸图标
- 📊 **同 App 自动去重** - 多版本只显示最新

## 🚀 快速开始

### 1. 部署

```bash
mkdir -p ipa-self-host && cd ipa-self-host
curl -O https://raw.githubusercontent.com/chinahhy/ipa-self-host-v2/main/docker-compose.yml
docker compose up -d
```

### 2. 配置

首次启动会自动生成 `./config/config.json` 模板，填入：
- `api_id`/`api_hash` - 从 https://my.telegram.org 申请
- `phone` - Telegram 注册手机号
- `groups` - 要扫描的群链接
- `whitelist` - 白名单 App 关键词

### 3. TG 首次登录

```bash
docker exec -it ipa-self-host /app/tg-login.sh
```

按提示输入验证码即可。

### 4. 订阅源

把 IPA 文件放入 `./data/ipa/`，5 秒后自动入源。

订阅 URL：`{REPO_BASE_URL}`（在 Esign 添加源即可）

## ⚙️ 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `REPO_BASE_URL` | 源的公网 URL | - |
| `REPO_NAME` | 源名称 | Private IPA Repo |
| `REPO_IDENTIFIER` | 源 BundleId | com.private.ipa.repo |
| `TG_PROXY` | TG 代理（socks5://host:port） | 空（直连） |
| `TG_SCAN_CRON` | TG 扫描 cron 表达式 | `0 1 * * *`（每天01:00） |
| `TG_SCAN_HOURS` | 回溯小时数 | 25 |

## 📁 目录结构

```
ipa-self-host/
├── docker-compose.yml
├── config/           # TG 配置（首次启动自动生成）
├── data/
│   ├── ipa/          # 放 IPA 文件
│   ├── icons/        # 自动提取的图标
│   └── repo.json     # 自动生成的源
├── session/          # TG 登录态
└── logs/             # 所有日志
```

## 🛠️ 维护

```bash
# 升级
docker compose pull && docker compose up -d

# 手动触发 TG 扫描
docker exec ipa-self-host /app/run-tg-scan.sh

# 手动重建 repo.json
docker exec ipa-self-host /app/scanner.py

# 查看日志
docker compose logs -f
tail -f logs/scanner.log
tail -f logs/tg-cron.log
```

## 📜 License

MIT
