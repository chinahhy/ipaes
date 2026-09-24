import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rootfs" / "app"))

from ipa_retention import archive_ipa, select_versions


def meta(bundle, version, filename=None, mtime=1):
    return {
        "bundleIdentifier": bundle,
        "version": version,
        "ipa_filename": filename or f"{bundle}_{version}.ipa",
        "mtime": mtime,
    }


class IpaRetentionTests(unittest.TestCase):
    def test_keeps_three_distinct_latest_versions_per_bundle(self):
        files = [meta("app.a", v) for v in ("1.0", "4.0", "2.0", "3.0")]
        files += [meta("app.b", v) for v in ("1.0", "2.0")]
        active, older = select_versions(files)
        self.assertEqual(
            {m["version"] for m in active if m["bundleIdentifier"] == "app.a"},
            {"2.0", "3.0", "4.0"},
        )
        self.assertEqual([m["version"] for m in older], ["1.0"])
        self.assertEqual(len([m for m in active if m["bundleIdentifier"] == "app.b"]), 2)

    def test_same_version_keeps_the_newer_file(self):
        files = [meta("app.a", "2.0", "old.ipa", 1), meta("app.a", "2.0", "new.ipa", 2)]
        active, older = select_versions(files)
        self.assertEqual([m["ipa_filename"] for m in active], ["new.ipa"])
        self.assertEqual([m["ipa_filename"] for m in older], ["old.ipa"])

    def test_protected_file_remains_active(self):
        protected = meta("app.x", "1.0", "X_10.76_证书安装登录版本.ipa")
        files = [protected] + [meta("app.x", v) for v in ("2.0", "3.0", "4.0")]
        active, older = select_versions(files)
        self.assertIn(protected, active)
        self.assertEqual(len(active), 3)
        self.assertEqual([m["version"] for m in older], ["2.0"])

    def test_archive_never_overwrites_an_older_archive(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            ipa_dir = Path(temp)
            (ipa_dir / "app.ipa").write_bytes(b"new")
            archive = ipa_dir / ".archive"
            archive.mkdir()
            (archive / "app.ipa").write_bytes(b"old")
            destination = archive_ipa(ipa_dir, "app.ipa")
            self.assertEqual(destination.name, "app__1.ipa")
            self.assertEqual(destination.read_bytes(), b"new")
            self.assertEqual((archive / "app.ipa").read_bytes(), b"old")


if __name__ == "__main__":
    unittest.main()
