# Spotify 图标与 X 白名单诊断

## [LRN-20261007-001] correction

**Logged**: 2026-10-07
**Priority**: medium
**Status**: resolved
**Area**: config

### Summary
Spotify 展示图标带卡通覆层时，复用生产已有 user.png 覆盖机制恢复 Apple 官方图标，无需改扫描器或部署镜像。

### Resolution
Apple lookup ID 324684580、bundle com.spotify.client；持 scanner 锁备份原图标后写入 user.png 与 png。实际解析 5 个活跃版本均保留覆盖，WebUI 与两份订阅图标 HTTP 200 且哈希一致；两份订阅仅目标 iconURL 的缓存参数改变。详见 `.release/spotify-icon-20261007/report.md`、`repair.py`、`verify-result.txt`。

### Metadata
- Source: user_feedback
- Related Files: rootfs/app/scanner.py
- See Also: .learnings/2026-10-05-netease-icon.md
- Pattern-Key: icons.branded-override-for-repackaged-app

## [LRN-20261007-002] knowledge_gap

**Logged**: 2026-10-07
**Priority**: medium
**Status**: resolved
**Area**: backend

### Summary
单字母独立词匹配仍会误收其他应用：BALL x PIT 文件名中的 x 命中 X/Twitter 白名单。

### Evidence and resolution
生产 match_whitelist 实测返回 X，tg-cron.log 记录 [X] 下载并 OK，文件在 X 目录。只从 keywords 删除 X 无效，因为 `_terms_for` 自动把 name=X 也作为关键词。

Hoya 后续明确要求修复白名单规则。修复单字符 ASCII 字母/数字关键词：仅允许其等于从文件名提取的完整应用名，禁止在其他名称或后缀中匹配；较长别名沿用原逻辑。74 项回归全部通过。只读对比生产全部 275 个 IPA 文件名，4 个变化均为误收：BALL x PIT、Blued X、Quantumult X 两个文件；其他匹配不变，8 个真实 X 文件（含 10.76）继续匹配。

Hoya 确认发布后，源码 0dc1727 经 GitHub Actions 37558305367 成功构建发布 latest。Docker Hub amd64/arm64 revision 均核验，index digest 为 sha256:8ce83e73bc42ea44e9570d2453e2d189eac6f6c04109dbdc08c4c6499e69ea18。NAS 已更新并验收：实际匹配器哈希正确，误收名称返回不匹配；正常 X/别名通过；WebUI 隐藏误收条目。容器 healthy、restarts=0，接口 200、IPA Range 206。配置、端口、挂载、X 10.76 内容哈希及 Spotify 覆盖图标保持不变，两份订阅除图标 URL 外逐字段语义校验一致。误收 IPA 文件未删除；不宣称后续定时扫描或真机客户端已经验收。

### Metadata
- Source: user_feedback
- Related Files: rootfs/app/ipa_matcher.py, tests/test_ipa_matcher.py
- Pattern-Key: whitelist.single-letter-position
- Verification artifacts: .release/whitelist-single-letter-20261007/tests.log, live-dry-run.json
- Fix commit: 0dc1727; production verification: .release/whitelist-single-letter-20261007/nas-verification.json
- Rollback: NAS ipaes 项目内 .deployment-backups/20261007-whitelist-single-letter/（旧镜像、只含镜像字段的 Compose 覆盖与脱敏校验信息；不复制凭据配置）

## [ERR-20261007-001] diagnostic-scope-and-tool-assumptions

**Logged**: 2026-10-07
**Priority**: medium
**Status**: resolved
**Area**: infra

### Summary
诊断时应使用已验证 NAS sudo 路径，避免 zsh 未匹配通配符，并严格限制日志输出范围。

### Resolution
沙箱 SSH 与普通 Docker socket 分别拒绝访问；正式审批的 SSH 配合既有 sudo -n docker 成功，见 2026-10-05 图标记录中的相同问题。改用 rg 在已知目录搜索，避免对不存在的 shell 通配符求值。NAS 精简容器没有 ps，可用宿主 Docker 状态或 /proc 核验。缺失的历史 README 通过 rg --files 定位现有记录。入库追查仅筛选 tg-cron 目标行并先脱敏；不要遍历输出 nginx-access.log，因为其请求路径可能含订阅鉴权段。

### Metadata
- Related Files: .learnings/2026-10-05-netease-icon.md, .release/spotify-icon-20261007/report.md
- Pattern-Key: diagnostics.minimal-redacted-production-evidence
