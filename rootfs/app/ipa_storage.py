"""App folders with stable basename URLs and preserved per-app archives."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import tempfile
import zipfile


def folder_name(value):
    value = re.sub(r'[\\/:\x00-\x1f<>|?*]', '_', str(value or ''))
    value = re.sub(r'\s+', ' ', value).strip(' .')[:120]
    return value or '未识别App'


def inferred_name(filename):
    name = str(filename).removesuffix('.part').removesuffix('.ipa')
    name = re.sub(r'^[🄷🅆\s]+', '', name)
    return re.split(r'[_ -]+v?\d|[（(]\d|(?<=\D)\d+\.\d', name, maxsplit=1)[0].strip(' _-') or name


def _regular(path, root):
    try:
        return not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def iter_ipas(root, archived=False):
    root = Path(root)
    if not root.is_dir():
        return []
    result = []
    parents = [root] + [p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith('.')]
    for parent in parents:
        directory = parent / '.archive' if archived else parent
        if directory.is_symlink():
            continue
        result.extend(p for p in directory.glob('*.ipa') if _regular(p, root))
    return sorted(result)


def resolve_ipa(root, filename, archived=False):
    if not filename or '/' in filename or '\\' in filename or '..' in filename or not filename.lower().endswith('.ipa'):
        return None
    matches = [p for p in iter_ipas(root, archived) if p.name == filename]
    return matches[0] if len(matches) == 1 else None


def identity(path):
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if re.fullmatch(r'Payload/[^/]+\.app/Info\.plist', n)]
            if not names:
                return {}
            info = plistlib.loads(z.read(names[0]))
        return {'name': info.get('CFBundleDisplayName') or info.get('CFBundleName'), 'bundleIdentifier': info.get('CFBundleIdentifier')}
    except (OSError, ValueError, zipfile.BadZipFile, plistlib.InvalidFileException):
        return {}


def _state_dir(root):
    return Path(os.environ.get("IPA_STORAGE_STATE_DIR") or root.parent)


def _read_registry(root):
    try:
        data = json.loads((_state_dir(root) / '.ipa-storage.json').read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _safe_folder(root, name):
    assert name == folder_name(name), 'Invalid app folder'
    path = root / name
    if path.is_symlink():
        raise OSError('App folder is a symlink')
    path.mkdir(parents=True, exist_ok=True)
    return path


def download_path(root, filename, app_name=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if '/' in filename or '\\' in filename or '..' in filename or not filename.lower().endswith('.ipa'):
        raise ValueError('Invalid IPA filename')
    existing = resolve_ipa(root, filename)
    if existing:
        return existing
    # A historical partial must continue in place, even before it has plist data.
    for parent in [root] + [p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith('.')]:
        partial = parent / (filename + '.part')
        if _regular(partial, root):
            return parent / filename
    registry = _read_registry(root)
    aliases = registry.get('aliases', {})
    name = app_name or inferred_name(filename)
    folder = aliases.get(name.casefold()) or aliases.get(inferred_name(filename).casefold()) or folder_name(name)
    return _safe_folder(root, folder) / filename


def _sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(b)
    return h.digest()


def store_ipa(path, root, meta=None, archived=False):
    """Move a complete IPA without overwriting a different existing version."""
    path, root = Path(path), Path(root)
    if path.is_symlink() or not path.is_file():
        raise OSError('IPA is not a regular file')
    meta = meta or identity(path)
    name = meta.get('name') or inferred_name(path.name)
    bundle = meta.get('bundleIdentifier') or ''
    root.mkdir(parents=True, exist_ok=True)
    state_dir = _state_dir(root)
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / '.ipa-storage.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        registry = _read_registry(root)
        bundles = registry.setdefault('bundles', {})
        aliases = registry.setdefault('aliases', {})
        existing_folder = path.parent.name if path.parent.parent == root and not path.parent.name.startswith('.') and path.parent.name != 'ipaes' else None
        folder = bundles.get(bundle) if bundle else None
        folder = folder or aliases.get(str(name).casefold()) or existing_folder or folder_name(name)
        parent = _safe_folder(root, folder)
        if archived:
            parent = parent / '.archive'
            if parent.is_symlink():
                raise OSError('Archive is a symlink')
            parent.mkdir(exist_ok=True)
        destination = parent / path.name
        if destination != path:
            if destination.exists():
                if destination.is_symlink() or not destination.is_file():
                    raise OSError('Destination is not a regular file')
                if destination.stat().st_size == path.stat().st_size and _sha(destination) == _sha(path):
                    path.unlink()
                else:
                    raise FileExistsError('Different IPA already occupies the app filename')
            else:
                path.rename(destination)
        if bundle:
            bundles[bundle] = folder
        for alias in (name, inferred_name(path.name)):
            aliases[str(alias).casefold()] = folder
        handle, temporary = tempfile.mkstemp(prefix='.ipa-storage.', dir=state_dir)
        try:
            with os.fdopen(handle, 'w') as f:
                json.dump(registry, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, state_dir / '.ipa-storage.json')
        finally:
            Path(temporary).unlink(missing_ok=True)
    return destination
