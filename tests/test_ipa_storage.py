import json
import os
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'rootfs/app'))
from ipa_storage import download_path, iter_ipas, resolve_ipa, store_ipa


def ipa(path, name='微信', bundle='com.tencent.xin', version='8.0.79'):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('Payload/App.app/Info.plist', plistlib.dumps({'CFBundleDisplayName':name,'CFBundleIdentifier':bundle,'CFBundleShortVersionString':version}))
    return path


class AppStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'ipa'
        self.root.mkdir()

    def test_complete_ipa_moves_to_its_app_and_keeps_basename_resolution(self):
        source = ipa(self.root / '微信_8.0.79.ipa')
        timestamp = source.stat().st_mtime_ns
        dest = store_ipa(source, self.root)
        self.assertEqual(dest, self.root / '微信' / source.name)
        self.assertEqual(dest.stat().st_mtime_ns, timestamp)
        self.assertEqual(resolve_ipa(self.root, source.name), dest)
        self.assertFalse(source.exists())
        self.assertEqual(download_path(self.root, '微信_8.0.80.ipa', '微信').parent, dest.parent)

    def test_existing_named_app_directory_is_reused_for_future_versions(self):
        existing = ipa(self.root / '今日水印相机--官方' / '今日水印相机_3.0.ipa', name='水印相机', bundle='camera.example')
        self.assertEqual(store_ipa(existing, self.root), existing)
        self.assertEqual(download_path(self.root, '今日水印相机_3.1.ipa', '今日水印相机').parent, existing.parent)

    def test_partial_resumes_in_its_folder(self):
        p = self.root / '微信' / '微信_8.0.79.ipa.part'
        p.parent.mkdir()
        p.write_bytes(b'partial')
        self.assertEqual(download_path(self.root, p.name[:-5], '微信'), p.with_name(p.name[:-5]))

    def test_archives_hidden_assets_certificates_and_symlinks_are_not_downloadable(self):
        active = ipa(self.root / '微信' / 'active.ipa')
        archived = ipa(self.root / '微信' / '.archive' / 'old.ipa')
        hidden = ipa(self.root / '.icons' / 'hidden.ipa')
        cert = self.root / '证书' / 'certificate.zip'
        cert.parent.mkdir(); cert.write_bytes(b'private')
        external = ipa(Path(self.temp.name) / 'outside.ipa')
        (self.root / '微信' / 'symlink.ipa').symlink_to(external)
        self.assertEqual(iter_ipas(self.root), [active])
        self.assertEqual(iter_ipas(self.root, archived=True), [archived])
        for name in ('old.ipa','hidden.ipa','certificate.zip','symlink.ipa','../outside.ipa','微信/active.ipa'):
            self.assertIsNone(resolve_ipa(self.root, name))

    def test_duplicate_basename_is_rejected_instead_of_serving_another_app(self):
        ipa(self.root / 'A' / 'same.ipa', name='A', bundle='a')
        ipa(self.root / 'B' / 'same.ipa', name='B', bundle='b')
        self.assertIsNone(resolve_ipa(self.root, 'same.ipa'))

    def test_different_existing_file_is_never_overwritten(self):
        original = store_ipa(ipa(self.root / 'same.ipa'), self.root)
        contents = original.read_bytes()
        incoming = ipa(self.root / 'same.ipa', version='8.0.80')
        with self.assertRaises(FileExistsError):
            store_ipa(incoming, self.root)
        self.assertEqual(original.read_bytes(), contents)
        self.assertTrue(incoming.exists())

    def test_identical_existing_file_is_deduplicated(self):
        original = store_ipa(ipa(self.root / 'same.ipa'), self.root)
        incoming = self.root / 'same.ipa'
        incoming.write_bytes(original.read_bytes())
        self.assertEqual(store_ipa(incoming, self.root), original)
        self.assertFalse(incoming.exists())

    def test_archive_migration_keeps_history_inside_app_folder(self):
        source = ipa(self.root / '.archive' / '微信_8.0.76.ipa')
        dest = store_ipa(source, self.root, archived=True)
        self.assertEqual(dest, self.root / '微信' / '.archive' / source.name)
        self.assertIsNone(resolve_ipa(self.root, source.name))


if __name__ == '__main__':
    unittest.main()
