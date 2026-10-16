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

## [LRN-20261016-002] correction

**Logged**: 2026-10-16
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Hoya 明确要求订阅版本与当前留存版本一致；诊断原因不能代替实现这个目标。

### Details
移除保留策略之后的 Bundle ID 最新版折叠，沿用已有平铺 apps 协议，为每个有效留存 IPA 发布独立条目。不改变版本保留、归档规则或每个条目的字段。参考现有协议来源 CN-CodeMan/AppStore 的 App.json，含同名多版本平铺条目。

### Metadata
- Source: user_feedback
- Related Files: rootfs/app/scanner.py, tests/test_scanner_retention.py
- See Also: LRN-20261016-001
- Pattern-Key: ipa-source.retention-versus-publication

### Resolution
- **Notes**: 75 项回归测试通过；包含重复扫描、逐版本说明和下载链接、两份源一致、归档不入源与 X 10.76 保护。NAS 只读候选预演：107 个当前留存文件对应 107 条订阅（原为 47 条），全部原有条目逐字段不变；小红书 5 个版本均入源，5 条下载链接返回 Range 206 且包含 ZIP 头。此为候选验证，生产发布和真机展示尚待验收。

## [ERR-20261016-002] candidate-module-file-context

**Logged**: 2026-10-16
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
通过 ModuleType 和 exec 预演依赖相对文件路径的候选模块时，必须设置 __file__。

### Error
候选 scanner 顶层使用 Path(__file__)，初次只读预演报 NameError: name '__file__' is not defined。

### Resolution
- **Notes**: 在内存候选模块上设置 __file__=/app/scanner.py 后预演成功；没有运行 scanner.scan 或改动 NAS 数据。

### Metadata
- Related Files: .release/source-versions-20261016/preview.py
- Pattern-Key: tests.exec-module-file-context
