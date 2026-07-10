import ast
import re
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TG_BOT_PATH = ROOT / "rootfs" / "app" / "tg_bot.py"


def _load_notification_builder():
    """只加载纯文本构造函数，避免测试依赖 Telethon。"""
    tree = ast.parse(TG_BOT_PATH.read_text(encoding="utf-8"))
    function_names = {"version_label", "compact_text", "build_notification_text"}
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in function_names
    ]
    namespace = {"datetime": datetime, "re": re}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(TG_BOT_PATH), "exec"), namespace)
    return namespace["build_notification_text"]


class TgBotNotificationTests(unittest.TestCase):
    def test_downloaded_apps_are_listed_once_without_cron_footer(self):
        build_notification_text = _load_notification_builder()
        downloaded = [
            {
                "app": "Infuse",
                "filename": "Infuse_8.2.0_iOS15系统版本.ipa",
                "size_mb": 70.5,
            },
            {
                "app": "Infuse",
                "filename": "Infuse_7.8.4_iOS14系统版本.ipa",
                "size_mb": 68.5,
            },
        ]

        text = build_notification_text(
            downloaded,
            total_ipa=12,
            total_dl=2,
            total_skipped=7,
            errors_count=0,
            groups_count=5,
            total_msgs=150,
        )

        self.assertIn("1. Infuse · v8.2.0 · 70.5 MB", text)
        self.assertIn("2. Infuse · v7.8.4 · 68.5 MB", text)
        self.assertNotIn("Infuse_8.2.0_iOS15系统版本.ipa", text)
        self.assertNotIn("Infuse_7.8.4_iOS14系统版本.ipa", text)
        self.assertNotIn("TG_SCAN_CRON", text)


if __name__ == "__main__":
    unittest.main()
