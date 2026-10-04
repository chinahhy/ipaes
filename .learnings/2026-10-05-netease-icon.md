# 网易云音乐图标修复

## [LRN-20261005-001] correction

**Logged**: 2026-10-05
**Priority**: medium
**Status**: resolved
**Area**: config

### Summary
展示为网易云音乐的轻量版 IPA 实际使用 bundle `sb.moe.kumone`，其声明的图标是黑金皮肤；正确提取安装包图标不等于满足用户期望的品牌图标。

### Resolution
- 从 Apple 官方目录（App Store ID 590338362、bundle com.netease.cloudmusic）获取 512×512 PNG，视觉核对为红白网易云图标。
- 生产数据卷中设置 `icons/sb.moe.kumone.user.png` 并同步 `icons/sb.moe.kumone.png`；沿用现有持久覆盖机制，无需修改扫描器、发布镜像或重启容器。
- 在 scanner 文件锁下操作，备份于生产容器 `/data/.icon-backups/20261005-netease/`。
- 两份订阅只更新该应用 iconURL 的 v 参数；逐字段比较其余内容完全相同。
- 调用生产 parse_ipa 验证再次解析仍使用覆盖图标；WebUI 图标接口与两份订阅图标 HTTP 200，内容 SHA256 均为 b38bb1fcdbb1266f4117714fbd11fcacb7bffaad6ef0c0f9eade3df1ca873def。WebUI API 命中唯一目标条目，healthz 200。
- 浏览器权限额度恢复后可打开 WebUI，但新会话停留登录页；未声称应用列表已完成浏览器视觉验收或签名客户端验收。
- 回退：持有 scanner 锁，恢复备份中的原图标；撤销本次新增的 user.png 覆盖，并针对当前两份订阅更新目标图标缓存版本。不要用旧整份订阅覆盖后续入库数据。

### Metadata
- Source: user_feedback
- Related Files: rootfs/app/scanner.py, .release/netease-icon-20261005/repair.py
- Verification artifacts: .release/netease-icon-20261005/ (local only)
- Prior work checkpoint: 105f3ff (existing native icon priority changes; not deployed by this task)
- Pattern-Key: icons.branded-override-for-repackaged-app

## [ERR-20261005-001] remote-image-transfer-argument-limit

**Logged**: 2026-10-05
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
把约 118 KB PNG 的 base64 作为 SSH 命令参数导致远端 Argument list too long，尚未执行修复脚本。

### Resolution
改为通过标准输入传送脚本及图片数据，命令参数保持短小。重新执行成功，生产文件与接口返回哈希一致。错误输出不要包含完整图像编码。

### Metadata
- Related Files: .release/netease-icon-20261005/repair.py
- Pattern-Key: ssh.binary-payload-over-stdin

## [ERR-20261005-002] nas-docker-read-permissions

**Logged**: 2026-10-05
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
沙箱内 SSH 的 Operation not permitted 与 NAS 普通账号的 Docker socket permission denied 是两层执行权限问题。

### Resolution
通过正式审批的 SSH 执行权限访问 NAS，再使用既有 `sudo -n docker`；只读容器探活成功。Git index 写入同样通过正式权限审批完成，不绕过沙箱或更改设备权限。
