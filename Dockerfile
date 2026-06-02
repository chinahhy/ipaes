# syntax=docker/dockerfile:1.6
# 单镜像：nginx + scanner + tg-bot 合一
# 基础镜像 debian-slim：有 cryptg 预编译 wheel，免 Rust 编译，构建快
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# 安装运行时（nginx + python3 + supervisord + cron + inotify）
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    python3 \
    python3-pip \
    supervisor \
    cron \
    inotify-tools \
    tzdata \
    bash \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Python 依赖（debian 上有 cryptg 预编译 wheel，秒装无需 Rust）
RUN pip install --no-cache-dir --break-system-packages \
    telethon==1.36.0 \
    cryptg==0.4.0 \
    PySocks==1.7.1 \
    flask==3.0.3

# 拷贝镜像内文件
COPY rootfs/ /

# 给脚本执行权限 + 创建必要目录
RUN chmod +x /app/*.sh /app/*.py 2>/dev/null || true \
    && mkdir -p /config /data /session /logs /data/ipa /data/icons \
    && chmod 755 /app \
    && rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true

# 暴露端口（80=订阅源 nginx, 8085=管理 webui）
EXPOSE 80 8085

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1/healthz || exit 1

# supervisord 接管所有进程（用我们的配置）
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
