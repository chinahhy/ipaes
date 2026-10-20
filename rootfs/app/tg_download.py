"""Resume Telegram files from complete chunks after a slow media DC."""

import asyncio
import re
from pathlib import Path


CHUNK_SIZE = 512 * 1024
# Telegram accepts up to 1 MiB per getFile request.  Larger requests cut the
# number of round trips on high-latency proxies while CHUNK_SIZE stays the
# resume granularity written to disk.
MAX_REQUEST_SIZE = 1024 * 1024


class DownloadStalled(TimeoutError):
    pass


class DownloadCancellationFailed(RuntimeError):
    pass


def premium_wait_seconds(error):
    """Telethon 1.36 does not recognize Telegram's download throttle."""
    if getattr(error, "code", None) != 420:
        return None
    match = re.fullmatch(r"FLOOD_PREMIUM_WAIT_(\d+)", str(getattr(error, "message", "")))
    return max(1, int(match.group(1))) if match else None


async def download_media_resumable(client, message, path: Path, expected_size,
                                   timeout, stall_timeout,
                                   request_size=MAX_REQUEST_SIZE):
    """Write complete 512 KiB chunks and restart at the last complete chunk.

    ``request_size`` controls the network read size (up to Telegram's 1 MiB
    limit); ``CHUNK_SIZE`` remains the on-disk resume alignment.
    """
    path = Path(path)
    try:
        request_size = int(request_size)
    except (TypeError, ValueError):
        request_size = MAX_REQUEST_SIZE
    request_size = max(CHUNK_SIZE, min(request_size, MAX_REQUEST_SIZE))
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
        message, offset=current, request_size=request_size,
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
                except Exception as exc:
                    wait_seconds = premium_wait_seconds(exc)
                    if wait_seconds is None:
                        raise
                    if wait_seconds >= deadline - loop.time():
                        raise asyncio.TimeoutError(f"总下载时间超过 {timeout} 秒") from exc
                    await asyncio.sleep(wait_seconds)
                    continue
                if not chunk or len(chunk) > expected_size - current:
                    raise RuntimeError(f"媒体块大小异常 {len(chunk)}/{expected_size - current}")
                output.write(chunk)
                output.flush()
                current += len(chunk)
        return current
    finally:
        # Telethon 1.36 initializes _sender inside the first __anext__ call.
        # If that initialization fails, its close() raises AttributeError and
        # hides the actual media-DC error. No sender exists to release yet.
        if not (type(stream).__module__.startswith("telethon.") and
                not hasattr(stream, "_sender")):
            cleanup = asyncio.create_task(stream.close())
            done, _ = await asyncio.wait({cleanup}, timeout=10)
            if not done:
                cleanup.cancel()
                raise DownloadCancellationFailed("媒体连接清理超时；停止本轮扫描")
            await cleanup
