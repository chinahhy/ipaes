# ipaes Agent Guide

## Engineering Cybernetics Inheritance

This project inherits the `Engineering Cybernetics Rules` section of
`~/.codex/AGENTS.md`: the top-level five questions, seven principles, the
6-step runtime checklist, anti-patterns, and the closed-loop final-report
contract.

- Meta-project source of truth: `~/Documents/Codex/控制中心`
- Runtime Skill: `engineering-cybernetics` (auto-triggers by description)
- Project control-loop spec: see `CONTROL-LOOP.md` at the repo root
- Project lifecycle records: `~/obsidian/Codex/<project>/`

Project-specific overrides below this section take precedence over the
global rules; write them only when the project genuinely needs to deviate.

## Project-specific overrides

- **铁律：X 10.76 永久保留，禁止删除**：保护对象固定为 `X_10.76_证书安装登录版本.ipa`（X/Twitter `10.76`，Build `14`，Bundle ID `com.atebits.Tweetie2`），不能随“最老版本”的变化改指其他版本；改名、迁移不解除保护。任何自动清理、版本淘汰、去重、归档清理、重下载或人工维护均不得删除、覆盖损坏或通过删除父目录/卷间接移除它。唯一例外：Hoya 明确要求删除该版本后，必须在同一次删除操作前逐轮取得 **3 次独立、明确的确认（1/3 → 2/3 → 3/3）**；每轮明确文件、实际路径和后果，等上一轮回复后再问下一轮。初始删除要求不计入三次确认；一次答复、历史授权、批量清理授权及沉默不能替代三轮。三轮未完成或中途撤回，不得删除或解除保护；删除范围变化需重新确认。详细约束见 `CONTROL-LOOP.md`。
- **不要重命名或随意覆盖 `unlock.json` / token 段**：已发布订阅的 URL 末段是用户在 Esign/AltStore 里写死的，必须沿用既有值（参见 commit `1428ed6`）。
- **修改 `repo.json` schema 视为高风险**：影响所有 AltStore/Esign 订阅客户端，需逐字段对比上线前后的 JSON。
- **保持单镜像单容器形态**：不要引入新的 service（数据库、独立 worker 等）；新增功能优先合入现有 supervisord 进程组。
- **真实 IPA / TG session / .env / config/ 永不入库**：`.gitignore` 已覆盖；CI 触发器仅在 `Dockerfile`、`.dockerignore`、`rootfs/**`、workflow 自身改动时构建镜像，文档与 compose 改动不构建。
- **图标资产**：`rootfs/app/webui_static/` 下的 `favicon.ico` / `icon-192.png` / `icon-512.png` / `icon-sunpanel.png` / `brand-icon.*` 是项目自有 mascot，禁止替换为受版权保护的第三方品牌素材。
- **NAS 部署语义**：用户说“本地容器”或“本地 NAS”时，默认指家用 NAS 上的 `ipaes` 容器；SSH 使用本机配置中的 `nas` 别名，不要按当前 Mac 的 Docker 环境处理。
- **远端部署**：默认 `nas` 的 Docker；任何远端服务重启 / 卷迁移 / 反代变更前先用无害命令探活并征得确认。
- **镜像发布与部署同步**：今后更新 NAS 生产容器时，同步将对应源码发布到 Docker Hub `hoya0803/ipaes`；优先通过 GitHub Actions 构建 amd64/arm64，核验 `latest` 的远端摘要及 NAS 运行内容。应急本地镜像部署后，必须补齐同版本发布并核对运行内容，才能报告更新完成。
- **镜像只用 latest**：Hoya 明确要求所有镜像发布与常规部署统一使用 `hoya0803/ipaes:latest`，禁止创建分支、版本号、SHA、临时或回退等自定义镜像标签。CI 与 Compose 均固定为 `latest`；版本核验使用摘要和 OCI revision，回退记录旧摘要/镜像 ID，不另打标签。

## Lifecycle records

- 项目目录：`~/Documents/Codex/ipaes/`
- Obsidian 记录：`~/obsidian/Codex/ipaes/`（按需建立）
