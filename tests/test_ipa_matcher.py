import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rootfs" / "app"))

from ipa_matcher import display_app_name, filename_app_name, match_whitelist


class IpaMatcherTests(unittest.TestCase):
    def test_filename_app_name_uses_prefix_before_version(self):
        self.assertEqual(
            filename_app_name("PiliPlus_2.0.9_哔哩哔哩.ipa"),
            "PiliPlus",
        )
        self.assertEqual(
            filename_app_name("聚合直播_1.12.6_净化广告.ipa"),
            "聚合直播",
        )

    def test_match_whitelist_prefers_specific_filename_prefix(self):
        whitelist = [
            {"name": "哔哩哔哩", "keywords": ["哔哩哔哩", "bilibili"]},
            {"name": "PiliPlus", "keywords": ["PiliPlus"]},
        ]

        self.assertEqual(
            match_whitelist("PiliPlus_2.0.9_哔哩哔哩.ipa", "", whitelist),
            "PiliPlus",
        )

    def test_display_name_uses_actual_package_prefix(self):
        self.assertEqual(
            display_app_name("PiliPlus_2.0.9_哔哩哔哩.ipa", "哔哩哔哩"),
            "PiliPlus",
        )


if __name__ == "__main__":
    unittest.main()
