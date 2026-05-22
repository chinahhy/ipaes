#!/bin/bash
# scanner-watcher: inotify监听 + 5秒防抖 + 10分钟兜底
# 注意：不能 set -e，因为软链接目录的 mkdir 会失败
set +e

WATCH_DIR=/data/ipa
DEBOUNCE_SEC=5
FALLBACK_INTERVAL=600

# 兼容软链接：只在不存在时建
[ -e "$WATCH_DIR" ] || mkdir -p "$WATCH_DIR"

scan_now() {
    echo "--- scan at $(date '+%Y-%m-%d %H:%M:%S') [trigger=$1] ---"
    /app/scanner.py 2>&1 || echo "❌ scan failed"
}

# 兜底定时器：每 10 分钟扫一次
(
    while true; do
        sleep $FALLBACK_INTERVAL
        echo "TRIGGER fallback" > /tmp/trigger
    done
) &

# inotify 监听
(
    inotifywait -m -e create -e moved_to -e moved_from -e delete -e close_write \
        --format "%e %f" "$WATCH_DIR" 2>/dev/null | \
    while read event filename; do
        case "$filename" in
            *.ipa) echo "TRIGGER $event:$filename" > /tmp/trigger ;;
            *) ;;
        esac
    done
) &

# 防抖循环
LAST_TRIGGER=""
while true; do
    if [ -f /tmp/trigger ]; then
        TRIGGER=$(cat /tmp/trigger)
        rm -f /tmp/trigger
        # 防抖：等 5 秒，期间有新事件则重新计时
        while true; do
            sleep $DEBOUNCE_SEC
            if [ -f /tmp/trigger ]; then
                TRIGGER=$(cat /tmp/trigger)
                rm -f /tmp/trigger
                continue
            fi
            break
        done
        scan_now "$TRIGGER"
    fi
    sleep 1
done
