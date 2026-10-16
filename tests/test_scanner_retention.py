import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rootfs" / "app"))

import scanner
from ipa_storage import iter_ipas


class ScannerRetentionIntegrationTests(unittest.TestCase):
    def test_scan_publishes_all_five_retained_versions_and_archives_sixth(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            data = Path(temp)
            ipa_dir = data / "ipa"
            ipa_dir.mkdir()
            cache = {}
            for number in range(1, 7):
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
            ), mock.patch.object(scanner.ipa_desc, "get", side_effect=lambda name: {
                "highlights": [f"Notes for {name}"], "source": f"Source for {name}",
            }), mock.patch.object(
                scanner.ipa_desc, "prune"
            ):
                scanner.scan()
                first_repo = json.loads((data / "repo.json").read_text())
                scanner.scan()
                repo = json.loads((data / "repo.json").read_text())
                altstore = json.loads((data / "_altstore.json").read_text())
                expected_entries = [
                    scanner.build_app_entry(cache[f"Example_{number}.0.ipa"]["meta"])
                    for number in range(6, 1, -1)
                ]

            self.assertEqual(len(iter_ipas(ipa_dir)), 5)
            self.assertTrue((ipa_dir / "Example" / ".archive" / "Example_1.0.ipa").exists())
            self.assertEqual(len(json.loads(cache_path.read_text())), 5)
            self.assertEqual(repo, first_repo, "Repeated scans must preserve all versions")
            self.assertEqual(repo["apps"], expected_entries)
            self.assertEqual(altstore["apps"], expected_entries)
            self.assertEqual(len(repo["apps"]), 5)
            self.assertEqual([a["version"] for a in repo["apps"]], ["6.0", "5.0", "4.0", "3.0", "2.0"])
            self.assertEqual(repo["apps"][0]["version"], "6.0")
            self.assertIn("Example_6.0.ipa", repo["apps"][0]["downloadURL"])
            for entry in repo["apps"]:
                filename = Path(unquote(urlsplit(entry["downloadURL"]).path)).name
                self.assertTrue((ipa_dir / "Example" / filename).is_file())
                self.assertEqual(entry["versionDescription"], f"• Notes for {filename}")
                self.assertEqual(entry["sourceMessage"], f"Source for {filename}")

    def test_protected_old_x_and_other_apps_are_all_published(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            data = Path(temp)
            ipa_dir = data / "ipa"
            ipa_dir.mkdir()
            metas = []
            protected_name = "X_10.76_证书安装登录版本.ipa"
            for index, (name, bundle, version, filename) in enumerate([
                ("X", "com.atebits.Tweetie2", "10.76", protected_name),
                *[("X", "com.atebits.Tweetie2", f"12.{i}", f"X_12.{i}.ipa") for i in range(1, 7)],
                ("Other", "com.example.other", "12.6", "Other_12.6.ipa"),
            ]):
                path = ipa_dir / filename
                with path.open("wb") as output:
                    output.truncate(1024 * 1024 + index)
                mtime = time.time() - 100 + index
                os.utime(path, (mtime, mtime))
                metas.append({
                    "name": name, "bundleIdentifier": bundle, "version": version,
                    "size": path.stat().st_size, "minOSVersion": "12.0",
                    "ipa_filename": filename, "icon_filename": None, "mtime": mtime,
                })
            cache = {m["ipa_filename"]: {
                "sig": scanner.file_signature(ipa_dir / m["ipa_filename"]),
                "icon_extractor": scanner.ICON_EXTRACTOR_VERSION, "meta": m,
            } for m in metas}
            cache_path = data / ".scan_cache.json"
            cache_path.write_text(json.dumps(cache), encoding="utf-8")
            with mock.patch.multiple(
                scanner, DATA_DIR=data, IPA_DIR=ipa_dir, ICONS_DIR=data / "icons",
                REPO_JSON=data / "repo.json", CACHE_DB=cache_path,
                SCAN_LOCK=data / ".scanner.lock", BASE_URL="https://example.test/repo",
            ), mock.patch.object(scanner.ipa_desc, "get", return_value={}), mock.patch.object(
                scanner.ipa_desc, "prune"
            ):
                scanner.scan()
                scanner.scan()
            repo = json.loads((data / "repo.json").read_text())
            altstore = json.loads((data / "_altstore.json").read_text())
            published = {
                Path(unquote(urlsplit(a["downloadURL"]).path)).name for a in repo["apps"]
            }
            retained = {p.name for p in iter_ipas(ipa_dir)}
            self.assertEqual(published, retained)
            self.assertEqual(len(published), 6)
            self.assertIn(protected_name, published)
            self.assertTrue((ipa_dir / "X" / protected_name).is_file())
            self.assertEqual(repo["apps"], altstore["apps"])
            self.assertNotIn("X_12.1.ipa", published)
            self.assertNotIn("X_12.2.ipa", published)
            self.assertIn("Other_12.6.ipa", published)

    def test_repo_write_failure_leaves_all_ipa_files_in_place(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            data = Path(temp)
            ipa_dir = data / "ipa"
            ipa_dir.mkdir()
            for number in range(1, 7):
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
            self.assertEqual(len(list(ipa_dir.glob("*.ipa"))), 6)
            self.assertFalse((ipa_dir / ".archive").exists())


if __name__ == "__main__":
    unittest.main()
