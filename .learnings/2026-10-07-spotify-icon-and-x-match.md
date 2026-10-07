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
**Status**: pending
**Area**: backend

### Summary
单字母独立词匹配仍会误收其他应用：BALL x PIT 文件名中的 x 命中 X/Twitter 白名单。

### Evidence and next action
生产 match_whitelist 实测返回 X，tg-cron.log 记录 [X] 下载并 OK，文件在 X 目录。本次诊断未授权修改匹配规则或清理游戏。后续修复应限定单字母应用名的位置，保留真实 X 和 NeoFreeBird/Twitter 别名，增加误收回归场景。只从 keywords 删除 X 无效，因为 `_terms_for` 自动把 name=X 也作为关键词。

### Metadata
- Source: user_feedback
- Related Files: rootfs/app/ipa_matcher.py, tests/test_ipa_matcher.py
- Pattern-Key: whitelist.single-letter-position

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
