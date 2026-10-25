import hashlib
from datetime import datetime
import io
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rootfs/app'))
import github_scan as gh


class Opener:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(request.full_url)
        return io.BytesIO(self.data)


def ipa():
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr('Payload/Test.app/Info.plist', plistlib.dumps({
            'CFBundleIdentifier': 'example.test', 'CFBundleShortVersionString': '1.0',
            'CFBundleDisplayName': 'Test', 'CFBundleVersion': '1'}))
    return data.getvalue()


def asset(data, number=1):
    return {'id': number, 'name': 'Test.ipa', 'size': len(data),
            'digest': 'sha256:' + hashlib.sha256(data).hexdigest(),
            'browser_download_url': 'https://github.com/owner/repo/releases/download/v1/Test.ipa'}


class GithubTests(unittest.TestCase):
    def test_normalization_and_host_restriction(self):
        self.assertEqual(gh.normalize_repo('https://github.com/Owner/Repo/releases/latest'), 'owner/repo')
        for value in ['https://evil.test/a/b', 'owner/..', '../repo', 'https://github.com/a/b?x=1', None]:
            with self.assertRaises(ValueError):
                gh.normalize_repo(value)

    def test_release_filter_and_five_version_bound(self):
        releases = [{'prerelease': True, 'assets': [asset(b'x', 999)]},
                    {'draft': True, 'assets': [asset(b'x', 998)]}]
        releases += [{'assets': [asset(b'x', n), {'name': 'android.apk'}]} for n in range(8)]
        found = list(gh.release_assets('owner/repo', Opener(json.dumps(releases).encode())))
        self.assertEqual([a['id'] for a in found], list(range(5)))

    def test_valid_intake_archive_dedupe_and_filename_isolation(self):
        data = ipa()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'ipa'
            opener = Opener(data)
            path = gh.ingest('owner/repo', asset(data), root, opener)
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertEqual(gh.identity(path)['bundleIdentifier'], 'example.test')
            archive = path.parent / '.archive'
            archive.mkdir()
            archived = archive / path.name
            path.rename(archived)
            self.assertEqual(gh.ingest('owner/repo', asset(data), root, opener), archived)
            self.assertEqual(len(opener.calls), 1)
            other = gh.ingest('owner/repo', asset(data, 2), root, opener)
            self.assertTrue(other.exists())
            self.assertNotEqual(other.name, archived.name)

    def test_intake_preserves_release_order_for_existing_source(self):
        data = ipa()
        releases = [
            {'published_at': '2026-10-25T00:00:00Z', 'assets': [asset(data, 2)]},
            {'published_at': '2026-10-20T00:00:00Z', 'assets': [asset(data, 1)]},
        ]
        found = list(gh.release_assets('owner/repo', Opener(json.dumps(releases).encode())))
        with tempfile.TemporaryDirectory() as temp:
            paths = [gh.ingest('owner/repo', item, Path(temp)/'ipa', Opener(data)) for item in found]
            self.assertGreater(paths[0].stat().st_mtime, paths[1].stat().st_mtime)
            self.assertEqual(paths[0].stat().st_mtime, datetime.fromisoformat('2026-10-25T00:00:00+00:00').timestamp())

    def test_invalid_size_digest_and_zip_leave_no_ipa(self):
        data = ipa()
        for bad_data, changes in [(b'not a zip', {}), (data, {'size': len(data)+1}),
                                  (data, {'digest': 'sha256:bad'}),
                                  (data, {'name': '../X.ipa'}),
                                  (data, {'browser_download_url': 'https://evil.test/x'})]:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / 'ipa'
                item = asset(bad_data)
                item.update(changes)
                with self.assertRaises(ValueError):
                    gh.ingest('owner/repo', item, root, Opener(bad_data))
                self.assertEqual(list(root.rglob('*.ipa')), [])
                self.assertEqual(list(root.rglob('*.part')), [])

    def test_state_survives_retention_and_failed_asset_retries(self):
        data = ipa()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / 'config.json'
            config.write_text(json.dumps({'github_repos': ['owner/repo']}))
            state = root / 'state.json'
            with patch.object(gh, 'release_assets', return_value=iter([asset(data)])), patch.object(gh, 'ingest', return_value=root/'test.ipa') as download:
                self.assertEqual(gh.scan(config, root/'ipa', state), 0)
                download.assert_called_once()
            with patch.object(gh, 'release_assets', return_value=iter([asset(data)])), patch.object(gh, 'ingest') as download:
                self.assertEqual(gh.scan(config, root/'ipa', state), 0)
                download.assert_not_called()

    def test_failed_asset_is_not_marked_downloaded(self):
        data = ipa()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / 'config.json'
            config.write_text(json.dumps({'github_repos': ['owner/repo']}))
            state = root / 'state.json'
            with patch.object(gh, 'release_assets', return_value=iter([asset(data)])), patch.object(gh, 'ingest', side_effect=ValueError('bad')):
                self.assertEqual(gh.scan(config, root/'ipa', state), 1)
                self.assertFalse(state.exists())
            with patch.object(gh, 'release_assets', return_value=iter([asset(data)])), patch.object(gh, 'ingest', return_value=root/'test.ipa') as download:
                self.assertEqual(gh.scan(config, root/'ipa', state), 0)
                download.assert_called_once()


if __name__ == '__main__':
    unittest.main()
