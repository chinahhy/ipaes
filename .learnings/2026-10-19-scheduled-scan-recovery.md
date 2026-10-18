## [LRN-20261019-001] best_practice

**Logged**: 2026-10-19T00:25:07+08:00
**Priority**: high
**Status**: resolved
**Area**: infra

### Summary
IPAes 容器 healthy、cron 进程运行、Telegram 账号连接成功，均不能单独证明扫描与通知业务正常。

### Details
本次 13:00 任务已触发，但代理连接失败使扫描在汇总前退出。排查时的新代理能通过 MTProto 账号验证，却不能访问 Bot API；另一代理的小样本通过，实际媒体传输仍很慢。应分别核验计划触发、账号连接、通知 API、实际字节增长和最终订阅链接，并读取 `/config/proxy.json` 与 cron 的覆盖变量，避免仅修改无实际优先权的环境变量或无效重启。

### Resolution
应用代理最终持久改为现有 `http://10.0.0.2:6666`。36 小时补扫（每群 500 条上限）完成 9 群，发现 34 个 IPA、下载 4 个、跳过 30 个，无异常；00:24:01 汇总发送成功。4 个 IPA 的完整 ZIP CRC、入源及 Range 206 验收通过，扫描进程退出并释放会话锁。X 10.76 内容、鉴权和订阅配置保持一致。未修改路由器、镜像或源码，未重启容器或常驻服务；后续真实定时轮次尚未发生。

### Metadata
- Source: verified_solution
- Related Files: rootfs/app/tg_bot.py, rootfs/app/run-tg-scan.sh
- Pattern-Key: ipaes.verify-scan-and-notification-paths
- Source checkpoint: 1b131d4
- Verified fix: .release/scheduled-scan-recovery-20261018/report.md
- Evidence: .release/scheduled-scan-recovery-20261018/verification.json, .release/scheduled-scan-recovery-20261018/runtime.txt
- See Also: .learnings/2026-10-16-source-version-visibility.md, ERR-20261016-001
- Execution boundary: 本次受限环境的 SSH 首次返回 Operation not permitted，按已记录的正式工具提权路径后成功；这不代表 NAS 网络故障，不修改设备权限。
