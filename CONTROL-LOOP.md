# CONTROL-LOOP.md · ipaes

> 项目控制环说明书。继承 `~/.codex/AGENTS.md` 的 Engineering Cybernetics
> Rules 与 `engineering-cybernetics` Skill；模板源在
> `/Users/hoya/Documents/Projects/控制中心/templates/control-loop-spec.md`。
>
> 修改顺序约定：本文件 → 项目根 `AGENTS.md` 的 Project-specific overrides
> → Obsidian `Codex/ipaes/` 决策记录。本文件保持简洁，决策细节
> 与历史背景写到 Obsidian。

---

## 1. 受控系统（Plant）

- 项目名：`ipaes`
- 一句话定义：自托管 iOS IPA 私有源；Esign / AltStore 兼容；TG 自动下载 + WebUI 管理；单镜像单容器部署。
- 物理边界：
  - 仓库：`/Users/hoya/Documents/Codex/ipaes/`
  - 镜像：`hoya0803/ipaes:latest`（Docker Hub）
  - 运行容器：HomeLab `homelab-nas` Docker 内的 `ipaes`
  - 数据卷：`./config /session /logs /data` 与可定制的 `IPA_DIR / ICONS_DIR`
  - 入口：宿主 `${HOST_PORT_NGINX:-8080}`（订阅源）/ `${HOST_PORT_WEBUI:-8085}`（WebUI）
- 时间边界：长期项目，按发布节奏滚动。

## 2. 系统边界与界面

- 输入接口：
  - 用户：WebUI（含登录态 + token URL 鉴权）、`docker exec /app/tg-login.sh` 首登
  - 数据：放入 `IPA_DIR` 的 IPA 文件、`config/config.json` 的 TG 群组与白名单
  - 上游事件：Telegram 群消息、cron 触发的回溯扫描、inotify 文件事件
- 输出接口：
  - `repo.json`（AltStore/Esign 协议）通过 nginx 暴露
  - IPA 直链下载（带 token URL 鉴权）
  - WebUI（控制台）
- 显式排除项：
  - 不做 IPA 签名/重签
  - 不维护非自有镜像（仅发布 `hoya0803/ipaes`）
  - 不在仓库内放任何真实 IPA、登录态、`.env`、`config/`

## 3. 目标态（Target State）

- 任务结束时为真：
  - 镜像可在 `linux/amd64,linux/arm64` 双架构上构建并通过 `docker compose up -d` 起来
  - WebUI `http://host:8085` 可登录，`/repo.json` 在 token 鉴权下可被 Esign/AltStore 订阅
  - `IPA_DIR` 新增/删除文件 5 秒内反映到 `repo.json` 与 WebUI
  - 项目附带原生图标资产（`favicon.ico` / `icon-192.png` / `icon-512.png` / `icon-sunpanel.png` / `brand-icon.*`），WebUI 与 Sun Panel 复用
- 必须保持不变：
  - 已发布的订阅 URL 末段（token / unlock 段）不被无脑覆盖（参见 `1428ed6`）
  - 用户卷 `./config ./session ./data` 的语义与权限
  - 单容器单镜像形态，不引入额外服务
- 可被外部观察到的成功判据：
  - `docker compose ps` 健康，`curl -fsS http://127.0.0.1:8080/healthz` 通过
  - WebUI 入口图标显示为本项目 mascot 而非默认浏览器图标

## 4. 反馈通道与采样

- 反馈来源：
  - 本地：编辑后 `python3 -m py_compile`、`docker compose config`、`docker build` 试构
  - 容器内：`/healthz`、supervisor 子进程状态、`logs/` 下日志
  - 远端：GitHub Actions `docker-publish.yml` 多架构构建结果
- 反馈延迟估计：
  - 本地编译/语法：秒级
  - 镜像构建（含 GHA）：5-15 分钟
  - 用户回流（订阅是否生效）：分钟到小时
- 计划采样粒度：每个有意义的功能/修复一次提交；高风险点（鉴权 / repo.json / 卷挂载）单独成 commit。
- 采样间状态外部化：
  - 短期：commit message + 本仓库 `README` / `DOCKERHUB_README`
  - 长期：`/Users/hoya/Documents/obsidian/Codex/ipaes/`

## 5. 控制律（Control Law）

- 主要干预手段：编辑 `rootfs/app/*.py`、`rootfs/etc/`、Dockerfile、compose、文档；通过 GHA 推镜像；HomeLab 端 `docker compose pull && up -d`。
- 单步上限：单 PR/commit 不混合「行为变更 + 重构 + 依赖升级」三类。
- 回退策略：
  - 镜像层：保留上一个稳定 tag，HomeLab 可 `image:` 指定回退
  - 配置层：`unlock.json` / `config.json` 改动前读旧值并保底
  - 数据层：永不在容器内主动删 `IPA_DIR`

## 6. 容差与裕度

- 容许失败模式：
  - TG 网络抖动 → cron 回溯 `TG_SCAN_HOURS` 兜底
  - 单个 IPA 解析失败 → 记录日志后跳过，不阻塞整源
  - 图标提取失败 → 回退到内置占位图
- 高风险点：
  - 鉴权（token URL / Basic Auth / session cookie）
  - `repo.json` schema（影响所有订阅客户端）
  - 卷挂载与权限（容器写入用户数据）
  - Docker Hub 凭据（仅在 GHA secrets 中）

## 7. 稳定性论证

- 已知耦合：
  - WebUI 与 scanner 共享 `/data` 目录与 `repo.json`
  - nginx 路径前缀由 `REPO_BASE_URL` 决定，改动会影响已分发的订阅
- 已知时滞最长：跨架构镜像构建 → 用户拉取 → HomeLab 部署
- 边界条件：
  - IPA 文件名包含特殊字符 / 多版本同 BundleId / CgBI 加密 PNG 图标
  - TG 代理失效时 fallback 到直连不可用
- 工作点之外的回退：
  - WebUI 出问题不影响订阅源（nginx + `repo.json` 独立）
  - scanner 崩溃由 supervisord 拉起

## 8. 验证清单

- 最窄检查：`python3 -m py_compile rootfs/app/*.py`、HTML/CSS 浏览器自检
- 中等检查：`docker compose config`、本地 `docker build .` 至少一架构通过
- 端到端检查：
  - 容器起来后 `curl http://127.0.0.1:8080/repo.json`（带 token）能返回合法 JSON
  - WebUI 登录、上传/感知 IPA、图标显示
  - Esign 真机订阅一次（人工，不每次跑）
- 不可验证项与替代证据：
  - TG 真实账号风控 → 用 commit log 与历史告警替代
  - 跨架构 arm64 行为 → 信赖 GHA 构建产物 + 用户回流

## 9. 关闭循环

- 任务结束时记录：变更 / 验证 / 不确定 / 下一步控制动作 → commit message + Obsidian。
- 教训归宿：
  - 通用工程教训 → `~/.codex/AGENTS.md` 或 Memory
  - 项目专有约束 → 本文件 + `AGENTS.md` overrides
  - 决策与里程碑 → `/Users/hoya/Documents/obsidian/Codex/ipaes/`
