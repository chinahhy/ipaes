import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rootfs" / "app"))

from ipa_retention import archive_ipa, is_protected, safe_unlink, select_versions


def meta(bundle, version, filename=None, mtime=1):
    return {
        "bundleIdentifier": bundle,
        "version": version,
        "ipa_filename": filename or f"{bundle}_{version}.ipa",
        "mtime": mtime,
    }


class IpaRetentionTests(unittest.TestCase):
    def test_keeps_five_distinct_latest_versions_per_bundle(self):
        files = [meta("app.a", v) for v in ("1.0", "4.0", "2.0", "6.0", "3.0", "5.0")]
        files += [meta("app.b", v) for v in ("1.0", "2.0")]
        active, older = select_versions(files)
        self.assertEqual(
            {m["version"] for m in active if m["bundleIdentifier"] == "app.a"},
            {"2.0", "3.0", "4.0", "5.0", "6.0"},
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
        files = [protected] + [meta("app.x", v) for v in ("2.0", "3.0", "4.0", "5.0", "6.0")]
        active, older = select_versions(files)
        self.assertIn(protected, active)
        self.assertEqual(len(active), 5)
        self.assertEqual([m["version"] for m in older], ["2.0"])

    def test_explicit_limit_still_controls_retention(self):
        files = [meta("app.a", str(v)) for v in range(1, 7)]
        active, older = select_versions(files, limit=3)
        self.assertEqual({m["version"] for m in active}, {"4", "5", "6"})
        self.assertEqual(len(older), 3)

    def test_protected_identity_survives_rename(self):
        protected = meta("com.atebits.Tweetie2", "10.76", "renamed_archive.ipa")
        files = [protected] + [
            meta("com.atebits.Tweetie2", v) for v in ("12.1", "12.2", "12.3", "12.4", "12.5")
        ]
        active, older = select_versions(files)
        self.assertIn(protected, active)
        self.assertEqual(len(active), 5)

    def test_safe_unlink_refuses_protected_filename_and_deletes_normal_file(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            protected = Path(temp) / "X_10.76_证书安装登录版本.ipa"
            normal = Path(temp) / "Other_1.0.ipa"
            protected.write_bytes(b"not-a-zip")
            normal.write_bytes(b"not-a-zip")
            self.assertFalse(safe_unlink(protected))
            self.assertTrue(protected.exists())
            self.assertTrue(safe_unlink(normal))
            self.assertFalse(normal.exists())

    def test_archive_refuses_protected_filename(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            path = Path(temp) / "X_10.76_证书安装登录版本.ipa"
            path.write_bytes(b"not-a-zip")
            with self.assertRaises(OSError):
                archive_ipa(Path(temp), path.name)
            self.assertTrue(path.exists())

    def test_identity_helper_flags_bundle_and_version(self):
        self.assertTrue(is_protected(None, "com.atebits.Tweetie2", "10.76", "14"))
        self.assertTrue(is_protected(None, "com.atebits.Tweetie2", "10.76.0", "14"))
        self.assertFalse(is_protected(None, "com.atebits.Tweetie2", "12.35", "14"))
        self.assertFalse(is_protected(None, "com.example.app", "10.76", "14"))

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
