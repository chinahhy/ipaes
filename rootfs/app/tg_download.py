"""Resume Telegram files from complete chunks after a slow media DC."""

import asyncio
from pathlib import Path


CHUNK_SIZE = 512 * 1024


class DownloadStalled(TimeoutError):
    pass


class DownloadCancellationFailed(RuntimeError):
    pass


async def download_media_resumable(client, message, path: Path, expected_size,
                                   timeout, stall_timeout):
    """Write complete 512 KiB chunks and restart at the last complete chunk."""
    path = Path(path)
    current = path.stat().st_size if path.exists() else 0
    if current > expected_size:
        with path.open("r+b") as output:
            output.truncate(0)
        current = 0
    elif current < expected_size and current % CHUNK_SIZE:
        current -= current % CHUNK_SIZE
        with path.open("r+b") as output:
            output.truncate(current)
    if current == expected_size:
        return current

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    stream = client.iter_download(
        message, offset=current, request_size=CHUNK_SIZE,
        chunk_size=CHUNK_SIZE, file_size=expected_size,
    )
    try:
        with path.open("r+b" if path.exists() else "wb") as output:
            output.seek(current)
            while current < expected_size:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError(f"总下载时间超过 {timeout} 秒")
                try:
                    chunk = await asyncio.wait_for(
                        stream.__anext__(), timeout=min(stall_timeout, remaining),
                    )
                except asyncio.TimeoutError as exc:
                    if deadline - loop.time() <= 0:
                        raise asyncio.TimeoutError(f"总下载时间超过 {timeout} 秒") from exc
                    raise DownloadStalled(f"连续 {stall_timeout} 秒无下载进度") from exc
                except StopAsyncIteration:
                    raise RuntimeError(f"媒体流提前结束 {current}/{expected_size}")
                if not chunk or len(chunk) > expected_size - current:
                    raise RuntimeError(f"媒体块大小异常 {len(chunk)}/{expected_size - current}")
                output.write(chunk)
                output.flush()
                current += len(chunk)
        return current
    finally:
        cleanup = asyncio.create_task(stream.close())
        done, _ = await asyncio.wait({cleanup}, timeout=10)
        if not done:
            cleanup.cancel()
            raise DownloadCancellationFailed("媒体连接清理超时；停止本轮扫描")
        await cleanup
