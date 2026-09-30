import ast
import logging
from pathlib import Path
import plistlib
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class ExistingIpaTests(unittest.TestCase):
    def setUp(self):
        source = ROOT / 'rootfs/app/tg_bot.py'
        tree = ast.parse(source.read_text())
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {'validate_ipa', 'validate_existing_ipa'}]
        self.ns = {'Path': Path, 'zipfile': zipfile, 'log': logging.getLogger('test-existing-ipa')}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), self.ns)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'Example_1.0.ipa'
        with zipfile.ZipFile(self.path, 'w') as z:
            z.writestr('Payload/Example.app/Info.plist', plistlib.dumps({'CFBundleIdentifier':'example.app'}))
            z.writestr('Payload/Example.app/Example', b'complete local binary')

    def test_smaller_remote_document_cannot_invalidate_complete_local_ipa(self):
        before = self.path.read_bytes()
        self.assertTrue(self.ns['validate_existing_ipa'](self.path, len(before)-40)[0])
        self.assertEqual(self.path.read_bytes(), before)

    def test_larger_remote_document_cannot_invalidate_complete_local_ipa(self):
        before = self.path.read_bytes()
        self.assertTrue(self.ns['validate_existing_ipa'](self.path, len(before)+40)[0])
        self.assertEqual(self.path.read_bytes(), before)

    def test_new_download_still_requires_exact_document_size(self):
        self.assertFalse(self.ns['validate_ipa'](self.path, self.path.stat().st_size+40)[0])

    def test_incomplete_local_file_still_fails_validation(self):
        self.path.write_bytes(self.path.read_bytes()[:30])
        self.assertFalse(self.ns['validate_existing_ipa'](self.path, 500)[0])
