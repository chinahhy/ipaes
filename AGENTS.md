# ipa-self-host-v2 Agent Guide

## Engineering Cybernetics Inheritance

This project inherits the `Engineering Cybernetics Rules` section of
`~/.codex/AGENTS.md`: the top-level five questions, seven principles, the
6-step runtime checklist, anti-patterns, and the closed-loop final-report
contract.

- Meta-project source of truth: `/Users/hoya/Documents/Projects/控制中心`
- Runtime Skill: `engineering-cybernetics` (auto-triggers by description)
- Project control-loop spec: see `CONTROL-LOOP.md` at the repo root
- Project lifecycle records: `/Users/hoya/Documents/obsidian/Codex/<project>/`

Project-specific overrides below this section take precedence over the
global rules; write them only when the project genuinely needs to deviate.

## Project-specific overrides

- **不要重命名或随意覆盖 `unlock.json` / token 段**：已发布订阅的 URL 末段是用户在 Esign/AltStore 里写死的，必须沿用既有值（参见 commit `1428ed6`）。
- **修改 `repo.json` schema 视为高风险**：影响所有 AltStore/Esign 订阅客户端，需逐字段对比上线前后的 JSON。
- **保持单镜像单容器形态**：不要引入新的 service（数据库、独立 worker 等）；新增功能优先合入现有 supervisord 进程组。
- **真实 IPA / TG session / .env / config/ 永不入库**：`.gitignore` 已覆盖；CI 触发器仅在 `Dockerfile`、`.dockerignore`、`rootfs/**`、workflow 自身改动时构建镜像，文档与 compose 改动不构建。
- **图标资产**：`rootfs/app/webui_static/` 下的 `favicon.ico` / `icon-192.png` / `icon-512.png` / `icon-sunpanel.png` / `brand-icon.*` 是项目自有 mascot，禁止替换为受版权保护的第三方品牌素材。
- **HomeLab 部署目标**：默认 `homelab-nas` 的 Docker；任何远端服务重启 / 卷迁移 / 反代变更前先用无害命令探活并征得确认。

## Lifecycle records

- 项目目录：`/Users/hoya/Documents/Projects/ipa-self-host-v2/`
- Obsidian 记录：`/Users/hoya/Documents/obsidian/Codex/ipa-self-host-v2/`（按需建立）
