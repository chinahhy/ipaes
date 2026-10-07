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

    def test_single_letter_app_name_matches_the_actual_app_prefix(self):
        whitelist = [{"name": "X", "keywords": ["NeoFreeBird"]}]
        for filename in (
            "X_12.28.1_NeoFreeBird.ipa",
            "X_12.31.2.ipa",
            "X_10.76_证书安装登录版本.ipa",
            "x-v12.31.2.ipa",
            "X v12.31.2.ipa",
            "X.ipa",
        ):
            with self.subTest(filename=filename):
                self.assertEqual(match_whitelist(filename, "", whitelist), "X")

    def test_x_rule_rejects_other_apps_with_an_x_in_their_names_or_suffixes(self):
        whitelist = [{"name": "X", "keywords": ["NeoFreeBird", "X", "Twitter"]}]
        for filename in (
            "BALL x PIT_1.301.ipa",
            "BALL X PIT_1.301.ipa",
            "BALL-x-PIT_1.301.ipa",
            "Blued X_7.50.3_彭于晏Crack.ipa",
            "Quantumult X_1.8.1_证书可使用.ipa",
            "Quantumult X_1.8.1-949_iOS27证书可用.ipa",
            "X Racer_1.0.ipa",
            "Package_1.0_X.ipa",
            "Netflix_1.0.ipa",
            "游戏x助手_1.0.ipa",
        ):
            with self.subTest(filename=filename):
                self.assertIsNone(match_whitelist(filename, "X Twitter NeoFreeBird", whitelist))

    def test_x_longer_aliases_still_match(self):
        whitelist = [{"name": "X", "keywords": ["NeoFreeBird", "X", "Twitter"]}]
        for filename in (
            "Twitter_10.76.ipa",
            "NeoFreeBird_12.31.2.ipa",
            "Package_12.31.2_NeoFreeBird.ipa",
            "Package_10.76_Twitter.ipa",
        ):
            with self.subTest(filename=filename):
                self.assertEqual(match_whitelist(filename, "", whitelist), "X")

    def test_other_single_character_keywords_cannot_match_inside_app_names(self):
        whitelist = [{"name": "Reader", "keywords": ["R"]}]
        self.assertIsNone(match_whitelist("Game R Edition_1.0.ipa", "", whitelist))
        self.assertIsNone(match_whitelist("Package_1.0_R.ipa", "", whitelist))
        self.assertEqual(match_whitelist("R_1.0.ipa", "", whitelist), "Reader")

    def test_explicit_game_whitelist_takes_precedence_over_x_rule(self):
        whitelist = [
            {"name": "X", "keywords": ["X", "Twitter"]},
            {"name": "BALL x PIT", "keywords": ["BALL x PIT"]},
        ]
        self.assertEqual(match_whitelist("BALL x PIT_1.301.ipa", "", whitelist), "BALL x PIT")


if __name__ == "__main__":
    unittest.main()
