"""Bounded Telegram media downloads with an inactivity watchdog."""

import asyncio


class DownloadStalled(TimeoutError):
    pass


class DownloadCancellationFailed(RuntimeError):
    pass


async def download_media_with_watchdog(client, message, path, timeout, stall_timeout):
    loop = asyncio.get_running_loop()
    started = last_progress = loop.time()
    task = None
    received = 0

    def on_progress(current, _total):
        nonlocal last_progress, received
        if current > received:
            received = current
            last_progress = loop.time()

    try:
        task = asyncio.create_task(
            client.download_media(message, file=str(path), progress_callback=on_progress)
        )
        while True:
            now = loop.time()
            if now - started >= timeout:
                raise asyncio.TimeoutError(f"总下载时间超过 {timeout} 秒")
            if now - last_progress >= stall_timeout:
                raise DownloadStalled(f"连续 {stall_timeout} 秒无下载进度")
            wait_for = min(5, timeout - (now - started), stall_timeout - (now - last_progress))
            done, _ = await asyncio.wait({task}, timeout=wait_for)
            if done:
                return await task
    finally:
        if task is not None and not task.done():
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=10)
            if not done:
                raise DownloadCancellationFailed("下载任务取消后仍未退出；停止本轮扫描")
