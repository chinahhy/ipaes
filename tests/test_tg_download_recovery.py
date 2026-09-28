import asyncio
import ast
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rootfs" / "app"))

from tg_download import (
    CHUNK_SIZE, DownloadCancellationFailed, DownloadStalled,
    download_media_resumable,
)
from tg_session_lock import SessionBusy, acquire_session_lock


class HangingClient:
    def __init__(self):
        self.cancelled = False
        self.closed = False

    def iter_download(self, _message, **_kwargs):
        return self

    async def __anext__(self):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def close(self):
        self.closed = True


class DownloadWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_download_is_cancelled(self):
        client = HangingClient()
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            with self.assertRaises(DownloadStalled):
                await download_media_resumable(
                    client, object(), Path(directory) / "unused.part", CHUNK_SIZE, 1, 0.03,
                )
        self.assertTrue(client.cancelled)
        self.assertTrue(client.closed)

    async def test_progress_keeps_download_alive(self):
        class ProgressStream:
            def __init__(self):
                self.chunks = iter((b"a" * CHUNK_SIZE, b"b" * CHUNK_SIZE, b"c" * 10))

            async def __anext__(self):
                await asyncio.sleep(0.025)
                try:
                    return next(self.chunks)
                except StopIteration:
                    raise StopAsyncIteration

            async def close(self):
                pass

        class ProgressClient:
            def iter_download(self, _message, **_kwargs):
                return ProgressStream()

        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "ok.part"
            result = await download_media_resumable(
                ProgressClient(), object(), path, CHUNK_SIZE * 2 + 10, 1, 0.05,
            )
            self.assertEqual(result, CHUNK_SIZE * 2 + 10)
            self.assertEqual(path.read_bytes()[-10:], b"c" * 10)

    async def test_existing_partial_chunk_is_rewound_and_resumed(self):
        class ResumeStream:
            async def __anext__(self):
                return b"b" * CHUNK_SIZE

            async def close(self):
                pass

        class ResumeClient:
            def __init__(self):
                self.offsets = []

            def iter_download(self, _message, **kwargs):
                self.offsets.append(kwargs["offset"])
                return ResumeStream()

        client = ResumeClient()
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "resume.part"
            path.write_bytes(b"a" * CHUNK_SIZE + b"broken-tail")
            await download_media_resumable(client, object(), path, CHUNK_SIZE * 2, 1, 0.05)
            self.assertEqual(client.offsets, [CHUNK_SIZE])
            self.assertEqual(path.read_bytes(), b"a" * CHUNK_SIZE + b"b" * CHUNK_SIZE)

    async def test_retry_keeps_partial_file_and_reconnects(self):
        source = (ROOT / "rootfs" / "app" / "tg_bot.py").read_text(encoding="utf-8")
        function = next(
            node for node in ast.parse(source).body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "download_one"
        )
        calls = []

        async def flaky_download(_client, _message, path, _size, _timeout, _stall):
            calls.append(path)
            if len(calls) == 1:
                path.write_bytes(b"par")
                raise ConnectionError("media DC disconnected")
            self.assertEqual(path.read_bytes(), b"par")
            path.write_bytes(b"valid")

        class ReconnectingClient:
            def __init__(self):
                self.disconnects = 0
                self.connects = 0

            async def disconnect(self):
                self.disconnects += 1

            async def connect(self):
                self.connects += 1

        namespace = {
            "asyncio": SimpleNamespace(
                sleep=lambda _seconds: asyncio.sleep(0), wait_for=asyncio.wait_for,
            ),
            "DOWNLOAD_ATTEMPTS": 2,
            "DOWNLOAD_TIMEOUT": 3600,
            "DOWNLOAD_STALL_TIMEOUT": 180,
            "MAX_CONCURRENT_DOWNLOADS": 1,
            "download_media_resumable": flaky_download,
            "validate_ipa": lambda path, size: (path.read_bytes() == b"valid", "bad"),
            "FloodWaitError": type("FloodWaitError", (Exception,), {}),
            "PeerFloodError": type("PeerFloodError", (Exception,), {}),
            "FileReferenceExpiredError": type("FileReferenceExpiredError", (Exception,), {}),
            "FilerefUpgradeNeededError": type("FilerefUpgradeNeededError", (Exception,), {}),
            "DownloadCancellationFailed": DownloadCancellationFailed,
            "log": logging.getLogger("test-tg-download"),
            "ipa_desc": SimpleNamespace(remember_from_message=lambda *args: None),
            "display_app_name": lambda filename, app: app,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), source, "exec"), namespace)
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            save_path = Path(directory) / "sample.ipa"
            item = {
                "filename": save_path.name, "app_name": "Sample", "size": 5,
                "save_path": save_path, "part_path": Path(str(save_path) + ".part"),
                "message": object(), "unique_key": "sample_5", "ver_key": "sample_1",
                "group_link": "group", "message_id": 1, "caption_text": "",
            }
            state = {"downloaded_files": [], "downloaded_versions": {}}
            client = ReconnectingClient()
            result = await namespace["download_one"](
                client, item, state, asyncio.Semaphore(1), asyncio.Event(),
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(calls), 2)
            self.assertEqual(save_path.read_bytes(), b"valid")
            self.assertFalse(item["part_path"].exists())
            self.assertEqual((client.disconnects, client.connects), (1, 1))


class SessionLockTests(unittest.TestCase):
    def test_second_owner_cannot_use_same_session(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "session.lock"
            with acquire_session_lock(path):
                with self.assertRaises(SessionBusy):
                    acquire_session_lock(path)
            with acquire_session_lock(path):
                pass


class ProxyOverrideTests(unittest.TestCase):
    def test_one_off_proxy_override_precedes_shared_config(self):
        source = (ROOT / "rootfs" / "app" / "tg_bot.py").read_text(encoding="utf-8")
        function = next(
            node for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "load_proxy_url"
        )
        namespace = {
            "os": os,
            "parse_proxy": lambda value: ("http", "10.0.0.5", 7893) if value.startswith("http://") else None,
            "PROXY_CONFIG_PATH": Path("/unused/proxy.json"),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), source, "exec"), namespace)
        with patch.dict(os.environ, {"TG_PROXY_OVERRIDE": "http://10.0.0.5:7893"}):
            self.assertEqual(namespace["load_proxy_url"](), "http://10.0.0.5:7893")


if __name__ == "__main__":
    unittest.main()
