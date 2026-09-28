import asyncio
import ast
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rootfs" / "app"))

from tg_download import DownloadCancellationFailed, DownloadStalled, download_media_with_watchdog
from tg_session_lock import SessionBusy, acquire_session_lock


class HangingClient:
    def __init__(self):
        self.cancelled = False

    async def download_media(self, _message, file, progress_callback):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class DownloadWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_download_is_cancelled(self):
        client = HangingClient()
        with self.assertRaises(DownloadStalled):
            await download_media_with_watchdog(client, object(), "unused.part", 1, 0.03)
        self.assertTrue(client.cancelled)

    async def test_progress_keeps_download_alive(self):
        class ProgressClient:
            async def download_media(self, _message, file, progress_callback):
                for n in range(3):
                    await asyncio.sleep(0.025)
                    progress_callback(n + 1, 3)
                return file

        result = await download_media_with_watchdog(
            ProgressClient(), object(), "ok.part", 1, 0.05,
        )
        self.assertEqual(result, "ok.part")

    async def test_retry_clears_partial_file_before_success(self):
        source = (ROOT / "rootfs" / "app" / "tg_bot.py").read_text(encoding="utf-8")
        function = next(
            node for node in ast.parse(source).body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "download_one"
        )
        calls = []

        async def flaky_download(_client, _message, path, _timeout, _stall):
            calls.append(path)
            if len(calls) == 1:
                path.write_bytes(b"partial")
                raise ConnectionError("media DC disconnected")
            self.assertFalse(path.exists())
            path.write_bytes(b"valid")

        namespace = {
            "asyncio": SimpleNamespace(sleep=lambda _seconds: asyncio.sleep(0)),
            "DOWNLOAD_ATTEMPTS": 2,
            "DOWNLOAD_TIMEOUT": 3600,
            "DOWNLOAD_STALL_TIMEOUT": 180,
            "download_media_with_watchdog": flaky_download,
            "validate_ipa": lambda path, size: (path.read_bytes() == b"valid", "bad"),
            "FloodWaitError": type("FloodWaitError", (Exception,), {}),
            "PeerFloodError": type("PeerFloodError", (Exception,), {}),
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
            result = await namespace["download_one"](
                object(), item, state, asyncio.Semaphore(1), asyncio.Event(),
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(calls), 2)
            self.assertEqual(save_path.read_bytes(), b"valid")
            self.assertFalse(item["part_path"].exists())


class SessionLockTests(unittest.TestCase):
    def test_second_owner_cannot_use_same_session(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "session.lock"
            with acquire_session_lock(path):
                with self.assertRaises(SessionBusy):
                    acquire_session_lock(path)
            with acquire_session_lock(path):
                pass


if __name__ == "__main__":
    unittest.main()
