import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rootfs" / "app"))

import scanner


class ScannerRetentionIntegrationTests(unittest.TestCase):
    def test_scan_archives_fourth_version_and_keeps_repo_url_on_latest(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            data = Path(temp)
            ipa_dir = data / "ipa"
            ipa_dir.mkdir()
            cache = {}
            for number in range(1, 5):
                filename = f"Example_{number}.0.ipa"
                path = ipa_dir / filename
                with path.open("wb") as output:
                    output.truncate(1024 * 1024 + number)
                mtime = time.time() - 100 + number
                os.utime(path, (mtime, mtime))
                cache[filename] = {
                    "sig": scanner.file_signature(path),
                    "icon_extractor": scanner.ICON_EXTRACTOR_VERSION,
                    "meta": {
                        "name": "Example", "bundleIdentifier": "com.example.app",
                        "version": f"{number}.0", "size": path.stat().st_size,
                        "minOSVersion": "12.0", "ipa_filename": filename,
                        "icon_filename": None, "mtime": mtime,
                    },
                }
            cache_path = data / ".scan_cache.json"
            cache_path.write_text(json.dumps(cache), encoding="utf-8")

            with mock.patch.multiple(
                scanner,
                DATA_DIR=data,
                IPA_DIR=ipa_dir,
                ICONS_DIR=data / "icons",
                REPO_JSON=data / "repo.json",
                CACHE_DB=cache_path,
                SCAN_LOCK=data / ".scanner.lock",
                BASE_URL="https://example.test/repo",
            ), mock.patch.object(scanner.ipa_desc, "get", return_value={}), mock.patch.object(
                scanner.ipa_desc, "prune"
            ):
                scanner.scan()
                scanner.scan()

            self.assertEqual(len(list(ipa_dir.glob("*.ipa"))), 3)
            self.assertTrue((ipa_dir / ".archive" / "Example_1.0.ipa").exists())
            self.assertEqual(len(json.loads(cache_path.read_text())), 3)
            repo = json.loads((data / "repo.json").read_text())
            self.assertEqual(len(repo["apps"]), 1)
            self.assertEqual(repo["apps"][0]["version"], "4.0")
            self.assertIn("Example_4.0.ipa", repo["apps"][0]["downloadURL"])

    def test_repo_write_failure_leaves_all_ipa_files_in_place(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            data = Path(temp)
            ipa_dir = data / "ipa"
            ipa_dir.mkdir()
            for number in range(1, 5):
                filename = f"Example_{number}.0.ipa"
                path = ipa_dir / filename
                with path.open("wb") as output:
                    output.truncate(1024 * 1024 + number)
                os.utime(path, (time.time() - 100, time.time() - 100))
            with mock.patch.multiple(
                scanner,
                DATA_DIR=data,
                IPA_DIR=ipa_dir,
                ICONS_DIR=data / "icons",
                REPO_JSON=data / "repo.json",
                CACHE_DB=data / ".scan_cache.json",
                SCAN_LOCK=data / ".scanner.lock",
                BASE_URL="https://example.test/repo",
            ), mock.patch.object(scanner, "parse_ipa", side_effect=lambda p: {
                "name": "Example", "bundleIdentifier": "com.example.app",
                "version": p.stem.split("_")[1], "size": p.stat().st_size,
                "minOSVersion": "12.0", "ipa_filename": p.name,
                "icon_filename": None, "mtime": p.stat().st_mtime,
            }), mock.patch.object(scanner, "atomic_write_json", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    scanner.scan()
            self.assertEqual(len(list(ipa_dir.glob("*.ipa"))), 4)
            self.assertFalse((ipa_dir / ".archive").exists())


if __name__ == "__main__":
    unittest.main()
