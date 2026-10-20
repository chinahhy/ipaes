import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rootfs" / "app"))

from jsonstore import atomic_write_json, load_json


class JsonStoreTests(unittest.TestCase):
    def test_atomic_write_replaces_complete_document(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            path = Path(temp) / "state.json"
            atomic_write_json(path, {"a": 1})
            atomic_write_json(path, {"a": 2, "b": "中文"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 2, "b": "中文"})
            self.assertEqual([p.name for p in path.parent.glob(".state.json.*")], [])

    def test_missing_file_returns_default(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            self.assertEqual(load_json(Path(temp) / "missing.json", {}), {})

    def test_corrupt_file_returns_default_without_recovery(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            path = Path(temp) / "state.json"
            path.write_text('{"broken": ', encoding="utf-8")
            self.assertEqual(load_json(path, {"fallback": True}), {"fallback": True})

    def test_corrupt_file_recovers_from_newest_valid_backup(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            path = Path(temp) / "config.json"
            (Path(temp) / "config.json.bak-20260101-000000").write_text(
                '{"old": true}', encoding="utf-8"
            )
            (Path(temp) / "config.json.bak-20260102-000000").write_text(
                '{"new": true}', encoding="utf-8"
            )
            (Path(temp) / "config.json.bak-20260103-000000").write_text(
                "not-json", encoding="utf-8"
            )
            path.write_text("{", encoding="utf-8")
            self.assertEqual(load_json(path, {}, recover=True), {"new": True})

    def test_write_is_readable_by_other_services(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            path = Path(temp) / "shared.json"
            atomic_write_json(path, {"ok": True})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)


if __name__ == "__main__":
    unittest.main()
