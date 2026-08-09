import ast
import asyncio
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional, cast

import httpx


ROOT = Path(__file__).resolve().parents[1]
FORWARD_BOT_PATH = ROOT / "rootfs" / "app" / "forward_bot.py"


def _load_functions(*names, extra: Optional[dict[str, Any]] = None):
    tree = ast.parse(FORWARD_BOT_PATH.read_text(encoding="utf-8"))
    selected: list[ast.stmt] = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    found = {node.name for node in selected if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = set(names) - found
    if missing:
        raise AssertionError(f"missing production functions: {sorted(missing)}")
    namespace: dict[str, Any] = {"re": re, "httpx": httpx}
    if extra:
        namespace.update(extra)
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(FORWARD_BOT_PATH), "exec"),
        namespace,
    )
    return namespace


class ForwardBotPollingTests(unittest.TestCase):
    def test_empty_timeout_exception_still_includes_type(self):
        namespace = _load_functions("_format_exception_for_log")
        format_exception = cast(Callable[..., str], namespace["_format_exception_for_log"])

        text = format_exception(httpx.ReadTimeout(""))

        self.assertIn("ReadTimeout", text)
        self.assertIn("detail=ReadTimeout('')", text)

    def test_exception_log_includes_only_type_not_arbitrary_message_or_connection_details(self):
        namespace = _load_functions("_format_exception_for_log")
        format_exception = cast(Callable[..., str], namespace["_format_exception_for_log"])
        user_message = "USER_MESSAGE_DO_NOT_LOG: please forward private payload"
        token = "123456:TEST_SECRET_TOKEN"
        proxy = "http://proxy-user:proxy-pass@10.0.0.2:7893"
        request_url = f"https://api.telegram.org/bot{token}/getUpdates?payload=private"
        exc = RuntimeError(
            f"{user_message}; request failed for {request_url} via {proxy}"
        )

        text = format_exception(exc, token, proxy)

        self.assertEqual("type=RuntimeError", text)
        self.assertNotIn(user_message, text)
        self.assertNotIn(token, text)
        self.assertNotIn(proxy, text)
        self.assertNotIn(request_url, text)

    def test_http_timeout_has_explicit_budget_for_long_polling(self):
        namespace = _load_functions("_build_http_timeout")
        build_timeout = cast(Callable[[float], httpx.Timeout], namespace["_build_http_timeout"])

        timeout = build_timeout(10.0)

        self.assertEqual(10.0, timeout.connect)
        self.assertEqual(45.0, timeout.read)
        self.assertEqual(10.0, timeout.write)
        self.assertEqual(10.0, timeout.pool)

    def test_poll_error_log_uses_sanitized_exception_details(self):
        user_message = "USER_MESSAGE_DO_NOT_LOG: private forwarded payload"
        token = "123456:TEST_SECRET_TOKEN"
        proxy = "http://proxy-user:proxy-pass@10.0.0.2:7893"
        request_url = f"https://api.telegram.org/bot{token}/getUpdates?payload=private"

        class StopPolling(BaseException):
            pass

        class FakeBot:
            calls = 0

            async def get_updates(self):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError(
                        f"{user_message}; request failed for {request_url} via {proxy}"
                    )
                raise StopPolling()

        class RecordingLog:
            def __init__(self):
                self.errors: list[str] = []
                self.infos: list[str] = []

            def error(self, message):
                self.errors.append(str(message))

            def info(self, message):
                self.infos.append(str(message))

        async def no_sleep(_seconds):
            return None

        logger = RecordingLog()
        namespace = _load_functions(
            "_format_exception_for_log",
            "poll_loop",
            extra={
                "TGClient": object,
                "BOT_TK": token,
                "PRX_URL": proxy,
                "log": logger,
                "asyncio": SimpleNamespace(sleep=no_sleep),
            },
        )
        poll_loop = cast(Callable[..., Any], namespace["poll_loop"])

        with self.assertRaises(StopPolling):
            asyncio.run(poll_loop(FakeBot()))

        text = "\n".join(logger.errors)
        self.assertIn("RuntimeError", text)
        self.assertNotIn(user_message, text)
        self.assertNotIn(token, text)
        self.assertNotIn(proxy, text)
        self.assertNotIn(request_url, text)

    def test_successful_empty_poll_emits_health_signal(self):
        class StopPolling(BaseException):
            pass

        class FakeBot:
            calls = 0

            async def get_updates(self):
                self.calls += 1
                if self.calls == 1:
                    return []
                raise StopPolling()

        class RecordingLog:
            def __init__(self):
                self.infos: list[str] = []

            def info(self, message):
                self.infos.append(str(message))

            def error(self, _message):
                pass

        async def no_sleep(_seconds):
            return None

        logger = RecordingLog()
        namespace = _load_functions(
            "_format_exception_for_log",
            "poll_loop",
            extra={
                "TGClient": object,
                "BOT_TK": "",
                "PRX_URL": "",
                "log": logger,
                "asyncio": SimpleNamespace(sleep=no_sleep),
                "time": SimpleNamespace(monotonic=lambda: 1000.0),
            },
        )
        poll_loop = cast(Callable[..., Any], namespace["poll_loop"])

        with self.assertRaises(StopPolling):
            asyncio.run(poll_loop(FakeBot()))

        self.assertTrue(
            any("getUpdates 正常" in message for message in logger.infos),
            logger.infos,
        )

    def test_success_health_log_is_suppressed_inside_300_seconds(self):
        class StopPolling(BaseException):
            pass

        class FakeBot:
            calls = 0

            async def get_updates(self):
                self.calls += 1
                if self.calls <= 2:
                    return []
                raise StopPolling()

        class RecordingLog:
            def __init__(self):
                self.infos: list[str] = []

            def info(self, message):
                self.infos.append(str(message))

            def error(self, _message):
                pass

        async def no_sleep(_seconds):
            return None

        monotonic_values = iter((1000.0, 1299.999))
        logger = RecordingLog()
        namespace = _load_functions(
            "_format_exception_for_log",
            "poll_loop",
            extra={
                "TGClient": object,
                "BOT_TK": "",
                "PRX_URL": "",
                "log": logger,
                "asyncio": SimpleNamespace(sleep=no_sleep),
                "time": SimpleNamespace(monotonic=lambda: next(monotonic_values)),
            },
        )
        poll_loop = cast(Callable[..., Any], namespace["poll_loop"])

        with self.assertRaises(StopPolling):
            asyncio.run(poll_loop(FakeBot()))

        health_logs = [message for message in logger.infos if "getUpdates 正常" in message]
        self.assertEqual(1, len(health_logs), logger.infos)

    def test_success_health_log_is_emitted_again_at_300_seconds(self):
        class StopPolling(BaseException):
            pass

        class FakeBot:
            calls = 0

            async def get_updates(self):
                self.calls += 1
                if self.calls <= 2:
                    return []
                raise StopPolling()

        class RecordingLog:
            def __init__(self):
                self.infos: list[str] = []

            def info(self, message):
                self.infos.append(str(message))

            def error(self, _message):
                pass

        async def no_sleep(_seconds):
            return None

        monotonic_values = iter((1000.0, 1300.0))
        logger = RecordingLog()
        namespace = _load_functions(
            "_format_exception_for_log",
            "poll_loop",
            extra={
                "TGClient": object,
                "BOT_TK": "",
                "PRX_URL": "",
                "log": logger,
                "asyncio": SimpleNamespace(sleep=no_sleep),
                "time": SimpleNamespace(monotonic=lambda: next(monotonic_values)),
            },
        )
        poll_loop = cast(Callable[..., Any], namespace["poll_loop"])

        with self.assertRaises(StopPolling):
            asyncio.run(poll_loop(FakeBot()))

        health_logs = [message for message in logger.infos if "getUpdates 正常" in message]
        self.assertEqual(2, len(health_logs), logger.infos)


if __name__ == "__main__":
    unittest.main()
