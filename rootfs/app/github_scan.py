#!/usr/bin/env python3
"""Public GitHub Releases intake; no Telegram session or extra service required."""
import fcntl
from datetime import datetime
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlparse, unquote, quote
import urllib.request
from urllib.error import HTTPError
import xml.etree.ElementTree as ET
import zipfile

from ipa_storage import identity, store_ipa, iter_ipas
from jsonstore import load_json, atomic_write_json


def error_label(exc):
    # Keep proxy credentials and signed download query strings out of logs.
    return f'HTTP {exc.code}' if isinstance(exc, HTTPError) else type(exc).__name__


def normalize_repo(value):
    if not isinstance(value, str):
        raise ValueError('请输入 GitHub 仓库地址或 owner/repo')
    value = value.strip().rstrip('/')
    if value.startswith('https://github.com/'):
        value = value[len('https://github.com/'):]
        value = re.sub(r'/releases(?:/latest)?$', '', value)
    value = value.removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+', value) or value.split('/')[1] in ('.', '..'):
        raise ValueError('仅支持公开 GitHub 仓库：owner/repo 或 https://github.com/owner/repo')
    return value.lower()


class ReleaseHTML(HTMLParser):
    """Read only GitHub's release badges, dates and asset rows."""
    def __init__(self):
        super().__init__()
        self.assets, self.item, self.spans = [], None, []
        self.prerelease, self.published = False, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'span':
            self.spans.append('Label' in attrs.get('class', ''))
        if tag == 'li':
            self.item = {}
        if tag == 'relative-time':
            self.published = self.published or attrs.get('datetime')
            if self.item is not None:
                self.item['updated_at'] = attrs.get('datetime')
        if self.item is not None:
            if tag == 'a' and attrs.get('href', '').lower().endswith('.ipa'):
                self.item['browser_download_url'] = 'https://github.com' + attrs['href']
                self.item['name'] = unquote(attrs['href'].rsplit('/', 1)[-1])
            if tag == 'clipboard-copy' and re.fullmatch(r'sha256:[0-9a-f]{64}', attrs.get('value', '')):
                self.item['digest'] = attrs['value']

    def handle_endtag(self, tag):
        if tag == 'span' and self.spans:
            self.spans.pop()
        if tag == 'li' and self.item is not None:
            if 'browser_download_url' in self.item:
                self.assets.append(self.item)
            self.item = None

    def handle_data(self, data):
        if any(self.spans) and data.strip() == 'Pre-release':
            self.prerelease = True


def web_release_assets(repo, opener):
    def read(url):
        with opener.open(urllib.request.Request(url, headers={'User-Agent': 'ipaes'}), timeout=60) as response:
            return response.read()
    feed = ET.fromstring(read(f'https://github.com/{repo}/releases.atom'))
    namespace = {'a': 'http://www.w3.org/2005/Atom'}
    count = 0
    for entry in feed.findall('a:entry', namespace):
        link = entry.find('a:link', namespace)
        url = link.get('href', '') if link is not None else ''
        prefix = f'https://github.com/{repo}/releases/tag/'
        if not url.lower().startswith(prefix):
            continue
        tag = quote(unquote(url[len(prefix):]), safe='')
        page = ReleaseHTML()
        page.feed(read(url).decode('utf-8'))
        if page.prerelease:
            continue
        assets = ReleaseHTML()
        assets.feed(read(f'https://github.com/{repo}/releases/expanded_assets/{tag}').decode('utf-8'))
        if assets.assets:
            for asset in assets.assets:
                yield {**asset, '_release_date': page.published}
            count += 1
            if count >= 5:
                return


def release_assets(repo, opener, limits=None):
    limits = limits if limits is not None else {}
    if limits.get('until', 0) > time.time():
        yield from web_release_assets(repo, opener)
        return
    try:
        yield from api_release_assets(repo, opener)
    except HTTPError as exc:
        if exc.code not in (403, 429) or (exc.code == 403 and exc.headers.get('X-RateLimit-Remaining') != '0'):
            raise
        # Normal public release pages remain usable when the REST API is rate limited.
        try:
            limits['until'] = max(float(exc.headers.get('X-RateLimit-Reset', 0)), time.time() + float(exc.headers.get('Retry-After', 60)))
        except ValueError:
            limits['until'] = time.time() + 60
        print(f'GitHub API 限流：{repo}，使用公开 Releases 网页', flush=True)
        yield from web_release_assets(repo, opener)


def api_release_assets(repo, opener):
    # Bounded history: five releases containing IPA, at most 100 releases.
    count = 0
    for page in range(1, 6):
        url = f'https://api.github.com/repos/{repo}/releases?per_page=20&page={page}'
        req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'ipaes'})
        with opener.open(req, timeout=60) as response:
            releases = json.load(response)
        if not isinstance(releases, list):
            raise ValueError('GitHub Releases 响应无效')
        for release in releases:
            if release.get('draft') or release.get('prerelease'):
                continue
            assets = [a for a in release.get('assets', []) if str(a.get('name', '')).lower().endswith('.ipa')]
            if assets:
                for asset in assets:
                    yield {**asset, '_release_date': release.get('published_at') or release.get('created_at')}
                count += 1
                if count >= 5:
                    return
        if len(releases) < 20:
            return


def ingest(repo, asset, root, opener, deadline=1800):
    url = asset['browser_download_url']
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.netloc != 'github.com' or not parsed.path.lower().startswith(f'/{repo}/releases/download/'):
        raise ValueError('非法 GitHub 附件地址')
    name = asset['name']
    if '/' in name or '\\' in name or '..' in name or any(ord(c) < 32 for c in name):
        raise ValueError('非法 IPA 文件名')
    # Both API and public HTML must identify an asset identically across scans.
    filename = f"gh_{repo.replace('/', '_')}_{asset_key(asset)}_{name[:-4]}.ipa"
    for path in iter_ipas(root) + iter_ipas(root, archived=True):
        if path.name == filename:
            return path
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    start = time.monotonic()
    fd, temporary = tempfile.mkstemp(prefix='.github-', suffix='.part', dir=root)
    try:
        total = 0
        with os.fdopen(fd, 'wb') as target:
            with opener.open(urllib.request.Request(url, headers={'User-Agent': 'ipaes'}), timeout=60) as response:
                size = asset.get('size')
                if size is None:
                    size = getattr(response, 'headers', {}).get('Content-Length')
                size = int(size) if size is not None else None
                if size is None and not asset.get('digest'):
                    raise ValueError('附件缺少大小与校验信息')
                while True:
                    if time.monotonic() - start > deadline:
                        raise TimeoutError('GitHub 下载超过 30 分钟')
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if size is not None and total > size:
                        raise ValueError('附件大小超出预期')
                    digest.update(block)
                    target.write(block)
        if size is not None and total != size:
            raise ValueError('附件下载不完整')
        expected = asset.get('digest')
        if expected and expected != 'sha256:' + digest.hexdigest():
            raise ValueError('附件 SHA-256 校验失败')
        meta = identity(temporary)
        if not meta.get('bundleIdentifier') or not meta.get('version'):
            raise ValueError('不是有效 IPA：缺少 Payload/Info.plist')
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip():
                raise ValueError('IPA ZIP 校验失败')
        # Rename inside a private staging folder so no existing IPA is overwritten.
        with tempfile.TemporaryDirectory(prefix='.github-stage-', dir=root) as staging:
            staged = Path(staging) / filename
            Path(temporary).rename(staged)
            staged.chmod(0o644)  # mkstemp is 0600; nginx must read the published IPA.
            # The existing source sorts versions by file mtime, not download order.
            date = asset.get('_release_date') or asset.get('updated_at')
            if date:
                timestamp = datetime.fromisoformat(date.replace('Z', '+00:00')).timestamp()
                os.utime(staged, (timestamp, timestamp))
            return store_ipa(staged, root, meta)
    finally:
        Path(temporary).unlink(missing_ok=True)


def asset_key(asset):
    value = asset['browser_download_url'] + ':' + (asset.get('digest') or asset.get('updated_at') or '')
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def scan(config=Path('/config/config.json'), root=Path('/data/ipa'), state_path=Path('/data/.github-downloads.json')):
    cfg = load_json(config, {}, recover=True)
    repos = cfg.get('github_repos', [])
    if not repos:
        return 0
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.with_suffix('.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('GitHub 扫描已在运行', flush=True)
            return 0
        proxy = load_json(Path('/config/proxy.json'), {})
        proxies = {'http': proxy['url'], 'https': proxy['url']} if proxy.get('enabled', True) and proxy.get('url') else {}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
        state = load_json(state_path, {})
        limit_path = state_path.with_suffix('.api-limit.json')
        limits = load_json(limit_path, {})
        errors = 0
        print(time.strftime('==== GitHub scan %Y-%m-%d %H:%M:%S ===='), flush=True)
        for value in repos:
            try:
                repo = normalize_repo(value)
                found = 0
                for asset in release_assets(repo, opener, limits):
                    found += 1
                    key = f"{repo}:{asset_key(asset)}"
                    if key in state:
                        continue
                    try:
                        path = ingest(repo, asset, root, opener)
                        state[key] = {'filename': path.name}
                        atomic_write_json(state_path, state)
                        print(f'GitHub 入库：{repo} / {asset["name"]}', flush=True)
                    except Exception as exc:
                        errors += 1
                        print(f'GitHub 附件失败：{repo} / {asset.get("name")} ({error_label(exc)})', flush=True)
                print(f'GitHub 扫描完成：{repo}，识别 {found} 个 IPA', flush=True)
            except Exception as exc:
                errors += 1
                print(f'GitHub 仓库失败：{value} ({error_label(exc)})', flush=True)
            if limits:
                atomic_write_json(limit_path, limits)
        return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(scan())
