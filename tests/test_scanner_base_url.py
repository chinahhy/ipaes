import ast
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from urllib.parse import quote, urlparse
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCANNER_PATH = ROOT / "rootfs" / "app" / "scanner.py"
APPLY_SCRIPT_PATH = ROOT / "rootfs" / "app" / "apply-repo-path.sh"


def _scanner_tree():
    return ast.parse(SCANNER_PATH.read_text(encoding="utf-8"))


def _load_resolver():
    tree = _scanner_tree()
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "resolve_base_url"
    ]
    if len(functions) != 1:
        raise AssertionError("resolve_base_url production function not found")
    selected: list[ast.stmt] = list(functions)
    namespace = {"os": os, "Path": Path, "urlparse": urlparse}
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(SCANNER_PATH), "exec"),
        namespace,
    )
    return namespace["resolve_base_url"]


def _load_app_entry_builder(base_url, icons_dir):
    tree = _scanner_tree()
    names = {"with_access_token", "build_app_entry"}
    selected: list[ast.stmt] = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    found = {
        node.name
        for node in selected
        if isinstance(node, ast.FunctionDef)
    }
    if found != names:
        raise AssertionError(f"missing production functions: {sorted(names - found)}")
    namespace = {
        "BASE_URL": base_url,
        "ICONS_DIR": icons_dir,
        "Path": Path,
        "ipa_desc": SimpleNamespace(get=lambda _filename: None),
        "iso_date": lambda _mtime: "2026-08-23T00:00:00.0000000Z",
        "quote": quote,
    }
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(SCANNER_PATH), "exec"),
        namespace,
    )
    return namespace["build_app_entry"]


class ScannerBaseUrlTests(unittest.TestCase):
    def setUp(self):
        self.resolve_base_url = _load_resolver()

    def test_base_url_assignment_uses_resolver(self):
        assignments = [
            node
            for node in _scanner_tree().body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "BASE_URL" for target in node.targets)
        ]
        self.assertEqual(1, len(assignments))
        value = cast(ast.Call, assignments[0].value)
        self.assertIsInstance(value, ast.Call)
        function = cast(ast.Name, value.func)
        self.assertIsInstance(function, ast.Name)
        self.assertEqual("resolve_base_url", function.id)

    def test_apply_script_publishes_applied_url_before_scanner_runs(self):
        script = APPLY_SCRIPT_PATH.read_text(encoding="utf-8")
        export_index = script.index('export REPO_BASE_URL="$FINAL_URL"')
        write_index = script.index('printf \'%s\\n\' "$FINAL_URL" > "$_APPLIED_TMP"')
        publish_index = script.index('mv -f "$_APPLIED_TMP" "$_APPLIED_FILE"')
        scan_index = script.index('/app/scanner.py >/dev/null 2>&1')
        self.assertLess(export_index, write_index)
        self.assertLess(write_index, publish_index)
        self.assertLess(publish_index, scan_index)

    def test_missing_applied_file_falls_back_to_environment_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            actual = self.resolve_base_url(
                "https://repo.example/startup/", applied_path=missing
            )
        self.assertEqual("https://repo.example/startup", actual)

    def test_valid_applied_url_overrides_stale_process_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            applied = Path(tmp) / "repo_base_url.applied"
            applied.write_text("  https://repo.example/current/\n", encoding="utf-8")
            actual = self.resolve_base_url(
                "https://repo.example/stale", applied_path=applied
            )
        self.assertEqual("https://repo.example/current", actual)

    def test_each_scan_can_observe_a_later_hot_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            applied = Path(tmp) / "repo_base_url.applied"
            applied.write_text("https://repo.example/first", encoding="utf-8")
            first = self.resolve_base_url(
                "https://repo.example/startup", applied_path=applied
            )
            applied.write_text("https://repo.example/second", encoding="utf-8")
            second = self.resolve_base_url(
                "https://repo.example/startup", applied_path=applied
            )
        self.assertEqual("https://repo.example/first", first)
        self.assertEqual("https://repo.example/second", second)

    def test_generated_resource_urls_follow_latest_applied_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            applied = root / "repo_base_url.applied"
            icons = root / "icons"
            icons.mkdir()
            icon_name = "com.example.test.png"
            (icons / icon_name).write_bytes(b"test-icon")
            meta = {
                "name": "Test App",
                "bundleIdentifier": "com.example.test",
                "version": "1.0",
                "size": 1024,
                "ipa_filename": "Test App.ipa",
                "icon_filename": icon_name,
                "mtime": 1,
            }

            for current in (
                "https://repo.example/first",
                "https://repo.example/second",
            ):
                with self.subTest(current=current):
                    applied.write_text(current, encoding="utf-8")
                    resolved = self.resolve_base_url(
                        "https://repo.example/stale", applied_path=applied
                    )
                    build_app_entry = _load_app_entry_builder(resolved, icons)
                    entry = build_app_entry(meta)
                    self.assertTrue(entry["iconURL"].startswith(f"{current}/icons/"))
                    self.assertTrue(entry["downloadURL"].startswith(f"{current}/ipa/"))
                    self.assertNotIn("/stale/", entry["iconURL"])
                    self.assertNotIn("/stale/", entry["downloadURL"])

    def test_empty_or_invalid_applied_value_does_not_replace_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            applied = Path(tmp) / "repo_base_url.applied"
            for invalid in ("", "relative/path", "ftp://repo.example/path"):
                with self.subTest(invalid=invalid):
                    applied.write_text(invalid, encoding="utf-8")
                    actual = self.resolve_base_url(
                        "https://repo.example/startup/", applied_path=applied
                    )
                    self.assertEqual("https://repo.example/startup", actual)

    def test_default_fallback_reads_repo_base_url_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            with mock.patch.dict(
                os.environ,
                {"REPO_BASE_URL": "https://repo.example/from-env/"},
            ):
                actual = self.resolve_base_url(applied_path=missing)
        self.assertEqual("https://repo.example/from-env", actual)


if __name__ == "__main__":
    unittest.main()
