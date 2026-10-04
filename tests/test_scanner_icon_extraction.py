import ast
import io
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNER_PATH = ROOT / "rootfs" / "app" / "scanner.py"


def _scanner_tree():
    return ast.parse(SCANNER_PATH.read_text(encoding="utf-8"))


def _load(names, namespace):
    tree = _scanner_tree()
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    found = {node.name for node in selected if isinstance(node, ast.FunctionDef)}
    missing = set(names) - found
    if missing:
        raise AssertionError(f"missing production functions: {sorted(missing)}")
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(SCANNER_PATH), "exec"),
        namespace,
    )
    return namespace


def _fake_png(size):
    # 只要文件头是 PNG 签名即可；_normalize_png 在测试里被 mock 成恒真。
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * (size - 8)


class IconStemTests(unittest.TestCase):
    def test_normalizes_extension_case_and_scale(self):
        ns = _load({"_icon_stem"}, {})
        f = ns["_icon_stem"]
        self.assertEqual(f("icon.png"), "icon")
        self.assertEqual(f("icon@2x.png"), "icon")
        self.assertEqual(f("Icon@3x.png"), "icon")
        self.assertEqual(f("AppIcon60x60@2x.png"), "appicon60x60")
        self.assertEqual(f("icon"), "icon")
        self.assertEqual(f("ProductionAppIcon76x76@2x~ipad.png"), "productionappicon76x76")


class ExtractLargestIconTests(unittest.TestCase):
    def _extractor(self):
        ns = {
            "shutil": shutil,
            # 候选选择测试不关心 CgBI 转换；恒真即可验证“选哪张图”。
            "_normalize_png": lambda _path: True,
        }
        _load({"_icon_stem", "extract_largest_icon"}, ns)
        return ns

    def _run(self, app_dir, plist, entries):
        ns = self._extractor()
        extract = ns["extract_largest_icon"]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(f"Payload/{app_dir}/Info.plist", b"<plist/>")
            for name, data in entries.items():
                zf.writestr(f"Payload/{app_dir}/{name}", data)
        buf.seek(0)
        with zipfile.ZipFile(buf, "r") as zf:
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "out.png"
                ok = extract(zf, app_dir, plist, out)
                content = out.read_bytes() if out.exists() else None
        return ok, content

    def test_declared_icon_with_png_extension_is_matched(self):
        # X/Twitter 12.31 的实际形态：CFBundleIconFiles 带 .png 扩展名。
        plist = {"CFBundleIconFiles": ["icon.png", "icon@2x.png", "icon@3x.png"]}
        entries = {
            "icon.png": _fake_png(64),
            "icon@2x.png": _fake_png(128),
            "icon@3x.png": _fake_png(256),
        }
        ok, content = self._run("Demo.app", plist, entries)
        self.assertTrue(ok, "declared icon.png should be extracted")
        self.assertEqual(len(content), 256, "largest declared icon wins")

    def test_lowercase_icon_fallback_when_not_declared(self):
        # 未声明 CFBundleIconFiles 时，根级小写 icon.png 也应作为兜底命中。
        plist = {}
        entries = {"icon.png": _fake_png(128)}
        ok, content = self._run("Demo.app", plist, entries)
        self.assertTrue(ok, "lowercase root icon.png should fall through")
        self.assertEqual(len(content), 128)

    def test_mango_tv_declared_icon_wins_over_larger_flutter_logo(self):
        plist = {
            "CFBundleIcons": {
                "CFBundlePrimaryIcon": {"CFBundleIconFiles": ["AppIcon60x60"]}
            },
            "CFBundleIcons~ipad": {
                "CFBundlePrimaryIcon": {
                    "CFBundleIconFiles": ["AppIcon60x60", "AppIcon76x76"]
                }
            },
        }
        ipad_icon = _fake_png(11451)
        entries = {
            "AppIcon60x60@2x.png": _fake_png(8480),
            "AppIcon76x76@2x~ipad.png": ipad_icon,
            "Frameworks/App.framework/flutter_assets/lib/shanhai/assets/images/logo_mgtv.png": _fake_png(27902),
        }
        ok, content = self._run("Mango.app", plist, entries)
        self.assertTrue(ok)
        self.assertEqual(content, ipad_icon)

    def test_declared_icon_wins_over_larger_deep_logo(self):
        icon = _fake_png(8480)
        entries = {
            "icon@2x.png": icon,
            "assets/logo.png": _fake_png(27902),
        }
        ok, content = self._run("Demo.app", {"CFBundleIconFiles": ["icon"]}, entries)
        self.assertTrue(ok)
        self.assertEqual(content, icon)

    def test_ipad_primary_declaration_wins_over_appicon_fallback(self):
        icon = _fake_png(128)
        plist = {"CFBundleIcons~ipad": {
            "CFBundlePrimaryIcon": {"CFBundleIconFiles": ["TabletPrimary"]}
        }}
        entries = {
            "TabletPrimary@2x~ipad.png": icon,
            "AppIconOther.png": _fake_png(512),
        }
        ok, content = self._run("Demo.app", plist, entries)
        self.assertTrue(ok)
        self.assertEqual(content, icon)

    def test_root_appicon_wins_over_larger_deep_logo_without_declaration(self):
        icon = _fake_png(8480)
        entries = {"AppIcon.png": icon, "assets/logo.png": _fake_png(27902)}
        ok, content = self._run("Demo.app", {}, entries)
        self.assertTrue(ok)
        self.assertEqual(content, icon)

    def test_deep_logo_still_works_without_native_icon(self):
        logo = _fake_png(27902)
        ok, content = self._run("Demo.app", {}, {"assets/logo.png": logo})
        self.assertTrue(ok)
        self.assertEqual(content, logo)

    def test_deep_logo_is_used_when_declared_icon_is_invalid(self):
        ns = self._extractor()
        ns["_normalize_png"] = lambda path: path.read_bytes() != b"invalid"
        logo = _fake_png(27902)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Payload/Demo.app/icon.png", b"invalid")
            zf.writestr("Payload/Demo.app/assets/logo.png", logo)
        buf.seek(0)
        with zipfile.ZipFile(buf, "r") as zf, tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.png"
            self.assertTrue(ns["extract_largest_icon"](
                zf, "Demo.app", {"CFBundleIconFiles": ["icon"]}, out
            ))
            self.assertEqual(out.read_bytes(), logo)

    def test_missing_icon_returns_false(self):
        plist = {}
        entries = {"background.png": _fake_png(512)}
        ok, _ = self._run("Demo.app", plist, entries)
        self.assertFalse(ok, "no icon-like PNG should fail extraction")


if __name__ == "__main__":
    unittest.main()
