# IPAes 订阅路径与手动 IPA 入库验收

## [LRN-20261009-001] knowledge_gap

**Logged**: 2026-10-09T16:04:28+08:00
**Priority**: medium
**Status**: resolved
**Area**: infra

### Summary

IPAes 的公网订阅 JSON 位于 `REPO_BASE_URL` 本身；不能在源地址后追加 `/repo.json`。实际 IPA 数据卷也应从运行容器挂载读取，不能按仓库 Compose 默认值猜测。

### Details

将某次新 IPA 加入 NAS 软件源时，按下载链接推导 `/<源路径>/repo.json` 做本地验收得到 HTTP 404。核对 `apply-repo-path.sh` 后确认 nginx 将 `/<源路径>` 直接映射至 `/data/repo.json`；`/<源路径>/ipa/<文件名>` 才是 IPA 下载路由。

### Suggested Action

新增 IPA 时先只读核实运行容器的 `/data/ipa` 挂载。完整文件用 `.part` 临时名上传，核对 SHA-256 与 ZIP 后原子改为 `.ipa`，再检查实际公网源 200、唯一 App 条目、IPA Range 206 和图标 PNG 200。不要输出私有订阅路径或令牌。

### Metadata

- Source: error
- Related Files: `rootfs/app/apply-repo-path.sh`, `rootfs/app/scanner.py`, `rootfs/app/ipa_storage.py`
- Tags: ipa-source, nginx-routing, nas, verification

### Resolution

- **Resolved**: 2026-10-09T16:04:28+08:00
- **Notes**: 按实际 nginx 路由复验；公网源含新增条目，下载支持 Range 206，图标返回有效 PNG。
