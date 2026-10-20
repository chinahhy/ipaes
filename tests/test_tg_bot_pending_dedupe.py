import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TG_BOT = ROOT / "rootfs" / "app" / "tg_bot.py"


def _load_functions(*names):
    tree = ast.parse(TG_BOT.read_text(encoding="utf-8"))
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    found = {node.name for node in selected}
    missing = set(names) - found
    if missing:
        raise AssertionError(f"missing production functions: {sorted(missing)}")
    namespace = {"re": re}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(TG_BOT), "exec"), namespace)
    return namespace


class PendingDedupeTests(unittest.TestCase):
    def test_version_label_accepts_spaces_parentheses_and_underscores(self):
        ns = _load_functions("version_label")
        label = ns["version_label"]
        self.assertEqual(label("Sparkle 1.5.1 (IG 410.1.0) [@ipavault].ipa"), "v1.5.1 (IG 410.1.0)")
        self.assertEqual(label("Spotify 9.1.90 (Vitrine+Pixie) [@ipavault].ipa"), "v9.1.90")
        self.assertEqual(label("X 12.35 NFB 7.2.0 [@ipavault].ipa"), "v12.35")
        self.assertEqual(label("微信_8.0.79_官替多合一插件.ipa"), "v8.0.79")
        self.assertEqual(label("Infuse_8.2.0_iOS15系统版本.ipa"), "v8.2.0")
        self.assertEqual(label("no-version.ipa"), "新版本")

    def test_same_version_variants_collapse_but_host_versions_do_not(self):
        ns = _load_functions("pending_version_key", "dedupe_pending_items")
        dedupe = ns["dedupe_pending_items"]
        items = [
            {"filename": "Sparkle 1.5.1 (IG 410.1.0) [@ipavault].ipa", "app_name": "Sparkle", "save_path": Path("/a")},
            {"filename": "Sparkle 1.5.1 (IG 410.1.0) No Plugins.ipa", "app_name": "Sparkle", "save_path": Path("/b")},
            {"filename": "Sparkle 1.5.1 (IG 451.0.0) [@ipavault].ipa", "app_name": "Sparkle", "save_path": Path("/c")},
            {"filename": "Spotify 9.1.90 (Vitrine+Pixie) [@ipavault].ipa", "app_name": "Spotify", "save_path": Path("/d")},
            {"filename": "Spotify 9.1.90 (Vitrine+Pixie) No Plugins.ipa", "app_name": "Spotify", "save_path": Path("/e")},
            {"filename": "X 12.35 NFB 7.1.1 [@ipavault].ipa", "app_name": "X", "save_path": Path("/f")},
            {"filename": "X 12.35 NFB 7.2.0 [@ipavault].ipa", "app_name": "X", "save_path": Path("/g")},
        ]
        kept, skipped = dedupe(items)
        self.assertEqual([str(item["save_path"]) for item in kept], ["/a", "/c", "/d", "/f"])
        self.assertEqual([str(item["save_path"]) for item, _ in skipped], ["/b", "/e", "/g"])

    def test_same_target_path_is_only_queued_once(self):
        ns = _load_functions("pending_version_key", "dedupe_pending_items")
        dedupe = ns["dedupe_pending_items"]
        shared = Path("/data/ipa/App/1.0.ipa")
        items = [
            {"filename": "App 1.0.ipa", "app_name": "App", "save_path": shared},
            {"filename": "App 1.0 (copy).ipa", "app_name": "App", "save_path": shared},
        ]
        kept, skipped = dedupe(items)
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0][1], "同路径已排队")


if __name__ == "__main__":
    unittest.main()
