# 软件源历史版本可见性排查

## [LRN-20261016-001] knowledge_gap

**Logged**: 2026-10-16T10:58:30+08:00
**Priority**: medium
**Status**: resolved
**Area**: backend

### Summary
本地保留五个版本不代表订阅发布五个版本：scanner 会在保留策略之后按 Bundle ID 仅选最新版。

### Details
只读核对 NAS 运行容器：小红书 9.49、9.49.1、9.49.4、9.49.5、9.50.1 均有缓存元数据及实际文件，Bundle ID 均为 com.xingin.discover。运行 scanner 的 by_bundle 只保留最新版；repo.json、_altstore.json 与实际公网订阅均只有 9.50.1，且无 versions 字段。WebUI /api/ipa 直接读取缓存和文件，因此能展示五个版本。无需将此现象归因于客户端缓存或文件丢失。

### Suggested Action
历史版本发布需另行核实目标客户端支持的源格式，再修改生成规则并逐字段比较两份 JSON；刷新客户端不能补回源中未发布的条目。本次仅诊断，没有修改生产服务或订阅 schema。

### Metadata
- Source: conversation
- Related Files: rootfs/app/scanner.py, rootfs/app/webui.py, rootfs/app/ipa_retention.py, tests/test_scanner_retention.py
- Pattern-Key: ipa-source.retention-versus-publication

### Resolution
- **Notes**: 已交叉验证运行源码、实际文件、两份源文件及公网响应；结论为当前发布策略所致，不是已实施的修复。

## [ERR-20261016-001] sandbox-permissions

**Logged**: 2026-10-16T10:58:30+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
默认沙箱拒绝内网 SSH 与 Git 索引写入；这不代表 NAS 故障或仓库损坏。

### Error
SSH 返回 Operation not permitted；Git 无法创建 .git/index.lock，返回 Operation not permitted。

### Suggested Fix
按原授权范围申请工具提权。此次相同只读 SSH 检查和 Git 检查点在提权后均成功；不要修改 SSH 密钥、NAS 配置或删除锁文件来绕过沙箱。

### Metadata
- Reproducible: yes
- Related Files: .git
- Pattern-Key: sandbox.distinguish-denied-operations

### Resolution
- **Notes**: 提权后连接成功，检查点 51455af 已创建。
