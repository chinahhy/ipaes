**IPAes 定时扫描恢复记录，2026-10-18 至 19 日（北京时间）**

已恢复扫描与汇总通知。最终应用代理为 `http://10.0.0.2:6666`，保存于 NAS 持久卷 `/config/proxy.json`。2026-10-18 23:52 启动的补扫于 2026-10-19 00:24:01 完成并发送汇总；00:25 的完整验收通过。

故障证据：

- 修复前最后成功扫描及通知为 10 月 17 日 16:35。容器于 10 月 18 日 01:15 启动，晚于当日 01:00 的计划点；日志没有该轮记录。
- 10 月 18 日 13:00 的 cron 确实触发，使用 `10.0.0.4:6666`，在 Telegram 连接阶段异常退出，尚未执行群扫描和汇总。检查时该端口拒绝连接，而该设备现有代理监听在 7893。
- 排查时持久配置已变为 `http://10.0.0.3:6666`。其 MTProto 账号验证通过，Bot API 请求却出现连接错误或超时，不能只凭账号能连接判定整条业务链正常。
- `10.0.0.4:7893` 的 Bot API、账号及媒体小样本通过，但实际下载仅约每分钟 1 MB。暂停本次手动补扫并保留断点后，改用已通过 Bot API 两次验证、账号验证及同一媒体 1 MB / 4.45 秒测试的 `10.0.0.2:6666`，随后完成全部实际下载。

修复与验证：

- 仅调整应用代理配置；使用既有 cron 环境执行一次 36 小时补扫，沿用每群最多 500 条消息和单文件串行下载。9 个群共发现 34 个 IPA，下载 4 个、跳过 30 个，本轮无异常。
- 汇总通知于 00:24:01 获 Bot API 成功响应；`state.json.last_scan` 更新为该时间。
- Infuse 8.5.8、Spotify 9.1.90、网易云音乐 9.6.09、Facebook Plus 1.0.2 四个文件全部通过 ZIP CRC 和 Payload/Info.plist 检查，均已发布到订阅，下载链接全部返回 HTTP Range 206 和 ZIP 文件头，无目标 `.part` 遗留。
- 两份订阅的 apps 一致，当前发布 108 个保留版本；原为 107 个。下载数量与订阅净增不同，仍沿用既有版本保留及归档规则。
- X 10.76 的文件大小、内容 SHA-256 与修复前完全一致。解锁配置、主配置、WebUI 账号配置、订阅路径和主机配置的内容哈希一致，活动订阅 URL 及字段结构不变。历史未完成文件保留。
- 容器 healthy、restarts=0，五个 supervisor 进程均 RUNNING；手动扫描进程已退出，会话锁已释放。

每日计划仍为 `0 1,13 * * *`，常规定时回溯仍为 25 小时，扫描代理覆盖变量为空，后续任务直接读取已保存的新代理。下一次真实计划点为 2026-10-19 01:00；本次验收时尚未发生。Bot API 接受发送已验证，手机实际收取界面、外部反代与 Esign/AltStore 真机未直接测试。

本次没有修改应用源码或更新镜像。运行引用仍为 `hoya0803/ipaes:latest`，镜像 ID 为 `sha256:6a92cc03b482ea8e9e92ea06d61ae7dde1e699ba600534457fcfd6f441ffe69c`，OCI revision 为 `cd4e0dd3edbfaf5b0c7f19f03dde54c64f504a5e`；没有执行容器或服务重启，也没有修改旁路由设置。源码检查点为本地提交 `1b131d4`。

回溯材料：

- NAS 容器内 `/data/.incident-backups/20261018-234742-scheduled-scan/` 保存不含凭据的原代理 JSON 和保护项基线。宿主位置为 `nas:/tmp/zfsv3/nvme11/17551075426a/data/Docker/ipaes/data/.incident-backups/20261018-234742-scheduled-scan/`，不包含 Telegram session、密码或 Token 备份。
- [完整验收结果](/Volumes/Data/ChatGPT项目文件/ipa软件包/.release/scheduled-scan-recovery-20261018/verification.json)、[运行状态](/Volumes/Data/ChatGPT项目文件/ipa软件包/.release/scheduled-scan-recovery-20261018/runtime.txt)、[只读验收脚本](/Volumes/Data/ChatGPT项目文件/ipa软件包/.release/scheduled-scan-recovery-20261018/verify.py)。
- 本地验收文件均位于当前项目内；无需项目外安装或清理。NAS 回溯目录可留作本次修复审计材料，未自动删除。
