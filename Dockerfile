# syntax=docker/dockerfile:1.6
# 多阶段构建：合并 nginx + scanner + tg-bot 到单一镜像
# 基础镜像：alpine（轻量，包含 nginx 和 python3）
FROM alpine:3.20

# 安装核心运行时
RUN apk add --no-cache \
    nginx \
    python3 \
    py3-pip \
    supervisor \
    dcron \
    inotify-tools \
    tzdata \
    bash \
    curl \
    ca-certificates \
    && rm -rf /var/cache/apk/*

# 安装 Python 依赖（Telethon + 代理 + 加密）
RUN pip install --no-cache-dir --break-system-packages \
    telethon==1.36.0 \
    cryptg==0.4.0 \
    PySocks==1.7.1

# 拷贝镜像内文件
COPY rootfs/ /

# 给脚本执行权限
RUN chmod +x /app/*.sh /app/*.py 2>/dev/null || true \
    && mkdir -p /config /data /session /logs /data/ipa /data/icons \
    && chmod 755 /app

# 暴露端口
EXPOSE 80

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1/healthz || exit 1

# supervisord 接管所有进程
ENTRYPOINT ["/app/entrypoint.sh"]
