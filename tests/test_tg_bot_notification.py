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
    function_names = {
        "version_label", "compact_text", "dedupe_notification_items",
        "build_notification_text",
    }
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in function_names
    ]
    namespace = {"datetime": datetime, "re": re}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(TG_BOT_PATH), "exec"), namespace)
    return namespace["build_notification_text"]


class TgBotNotificationTests(unittest.TestCase):
    def test_empty_window_does_not_claim_whitelisted_versions_are_stored(self):
        build_notification_text = _load_notification_builder()
        text = build_notification_text([], 0, 0, 0, 0, groups_count=1, total_msgs=20)
        self.assertIn("没有发现 .ipa 附件", text)
        self.assertNotIn("已在库", text)

    def test_unmatched_attachments_are_reported_as_whitelist_misses(self):
        build_notification_text = _load_notification_builder()
        text = build_notification_text([], 2, 0, 2, 0, matched_count=0)
        self.assertIn("没有命中白名单", text)

    def test_different_versions_are_each_listed_without_cron_footer(self):
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

    def test_same_app_version_from_different_groups_is_listed_once(self):
        build_notification_text = _load_notification_builder()
        downloaded = [
            {
                "app": "Infuse",
                "filename": "Infuse_8.2.0_group-a.ipa",
                "size_mb": 70.5,
                "ver_key": "Infuse_8.2.0",
            },
            {
                "app": "Infuse",
                "filename": "Infuse_8.2.0_group-b.ipa",
                "size_mb": 71.5,
                "ver_key": "Infuse_8.2.0",
            },
        ]

        text = build_notification_text(
            downloaded,
            total_ipa=2,
            total_dl=2,
            total_skipped=0,
            errors_count=0,
            groups_count=2,
            total_msgs=20,
        )

        self.assertIn("入库 2 个", text)
        self.assertEqual(text.count("Infuse · v8.2.0"), 1)
        self.assertNotIn("71.5 MB", text)

    def test_same_version_for_different_apps_is_not_collapsed(self):
        build_notification_text = _load_notification_builder()
        downloaded = [
            {"app": "App A", "filename": "AppA_1.2.3_a.ipa", "size_mb": 10},
            {"app": "App B", "filename": "AppB_1.2.3_b.ipa", "size_mb": 20},
        ]

        text = build_notification_text(
            downloaded, 2, 2, 0, 0, groups_count=2, total_msgs=20,
        )

        self.assertIn("App A · v1.2.3", text)
        self.assertIn("App B · v1.2.3", text)

    def test_unknown_versions_with_different_filenames_are_not_collapsed(self):
        build_notification_text = _load_notification_builder()
        downloaded = [
            {"app": "Infuse", "filename": "Infuse_build-a.ipa", "size_mb": 10},
            {"app": "Infuse", "filename": "Infuse_build-b.ipa", "size_mb": 20},
        ]

        text = build_notification_text(
            downloaded, 2, 2, 0, 0, groups_count=2, total_msgs=20,
        )

        self.assertEqual(text.count("Infuse · 新版本"), 2)

    def test_overflow_count_uses_unique_notification_items(self):
        build_notification_text = _load_notification_builder()
        downloaded = [
            {
                "app": f"App {index}",
                "filename": f"App{index}_1.0.0_group-a.ipa",
                "size_mb": index,
            }
            for index in range(9)
        ]
        downloaded.append({
            "app": "App 0",
            "filename": "App0_1.0.0_group-b.ipa",
            "size_mb": 99,
        })

        text = build_notification_text(
            downloaded, 10, 10, 0, 0, groups_count=2, total_msgs=20,
        )

        self.assertIn("...还有 1 个新包", text)
        self.assertNotIn("...还有 2 个新包", text)


if __name__ == "__main__":
    unittest.main()
