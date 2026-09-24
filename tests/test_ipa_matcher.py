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

    def test_caption_cannot_admit_a_different_app(self):
        whitelist = [
            {"name": "抖音", "keywords": ["抖音", "Douyin", "抖音短视频"]},
        ]

        self.assertIsNone(
            match_whitelist(
                "极光之恋_1.1.51_Release.ipa",
                "极光之恋——抖音刷礼物模拟器",
                whitelist,
            )
        )

    def test_full_filename_still_matches_a_whitelist_alias(self):
        whitelist = [
            {"name": "抖音", "keywords": ["抖音", "Douyin", "抖音短视频"]},
        ]

        self.assertEqual(
            match_whitelist("Package_1.0_抖音.ipa", "", whitelist),
            "抖音",
        )

    def test_display_name_uses_actual_package_prefix(self):
        self.assertEqual(
            display_app_name("PiliPlus_2.0.9_哔哩哔哩.ipa", "哔哩哔哩"),
            "PiliPlus",
        )

    def test_single_letter_app_name_matches_only_a_separate_token(self):
        whitelist = [{"name": "X", "keywords": ["NeoFreeBird"]}]
        self.assertEqual(
            match_whitelist("X_12.28.1_NeoFreeBird.ipa", "#X", whitelist),
            "X",
        )
        self.assertIsNone(match_whitelist("Netflix_1.0.ipa", "", whitelist))


if __name__ == "__main__":
    unittest.main()
