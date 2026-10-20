"""Keep five distinct installed versions per Bundle ID; archive older IPA files.

The X/Twitter 10.76 build is a permanent exception.  Protection is matched by
bundle/version identity as well as by the historical filename, because renaming
or moving the file must not lift the protection (see the project AGENTS.md).
"""

import re
from collections import defaultdict
from pathlib import Path

from ipa_storage import identity, resolve_ipa


KEEP_VERSIONS = 5
PROTECTED_FILES = {"X_10.76_证书安装登录版本.ipa"}
PROTECTED_BUNDLE = "com.atebits.Tweetie2"
PROTECTED_VERSION = "10.76"
PROTECTED_BUILD = "14"
# Fallback for damaged/unreadable files: never delete a name that clearly
# points at the protected X/Twitter 10.76 release.
_PROTECTED_NAME_HINTS = ("10.76",)
_PROTECTED_APP_HINTS = ("x_", "x ", "x-", "twitter", "tweetie")


def version_order(version):
    numbers = [int(part) for part in re.findall(r"\d+", str(version or ""))]
    return tuple((numbers + [0] * 8)[:8])


def _version_is_protected(version) -> bool:
    value = str(version or "").strip()
    return value == PROTECTED_VERSION or value.startswith(f"{PROTECTED_VERSION}.")


def _name_is_protected(filename) -> bool:
    name = str(filename or "").strip()
    if not name:
        return False
    if name in PROTECTED_FILES:
        return True
    folded = name.casefold()
    return (
        any(hint in folded for hint in _PROTECTED_NAME_HINTS)
        and any(hint in folded for hint in _PROTECTED_APP_HINTS)
    )


def is_protected(filename=None, bundle=None, version=None, build=None) -> bool:
    """Return True when this IPA is the permanent X/Twitter 10.76 release."""
    if filename and _name_is_protected(filename):
        return True
    if str(bundle or "").strip() != PROTECTED_BUNDLE:
        return False
    if _version_is_protected(version):
        return True
    # X 10.76 is build 14; keep the build as a secondary identity in case a
    # cache entry lacks the short version string.
    return str(build or "").strip() == PROTECTED_BUILD and not str(version or "").strip()


def is_protected_path(path: Path) -> bool:
    """Best-effort identity check for a file about to be deleted/moved."""
    path = Path(path)
    if _name_is_protected(path.name):
        return True
    meta = identity(path)
    return is_protected(
        None, meta.get("bundleIdentifier"), meta.get("version"), meta.get("build")
    )


def safe_unlink(path: Path) -> bool:
    """Delete one IPA unless it is protected. Returns True when removed."""
    path = Path(path)
    if is_protected_path(path):
        return False
    path.unlink()
    return True


def select_versions(metas, limit=KEEP_VERSIONS):
    """Return (active, older) metadata, grouping by the IPA's Bundle ID."""
    groups = defaultdict(list)
    for meta in metas:
        bundle_id = meta.get("bundleIdentifier")
        if not bundle_id or bundle_id == "unknown.bundle.id":
            bundle_id = meta["ipa_filename"]
        groups[bundle_id].append(meta)

    active, older = [], []
    for group in groups.values():
        group.sort(
            key=lambda m: (version_order(m.get("version")), m.get("mtime", 0)),
            reverse=True,
        )
        selected_versions = set()
        protected = [
            m
            for m in group
            if is_protected(
                m.get("ipa_filename"),
                m.get("bundleIdentifier"),
                m.get("version"),
                m.get("build"),
            )
        ]
        for meta in protected:
            selected_versions.add(str(meta.get("version", "")).casefold())
        active.extend(protected)

        for meta in group:
            if meta in protected:
                continue
            version = str(meta.get("version", "")).casefold()
            if version in selected_versions or len(selected_versions) >= limit:
                older.append(meta)
            else:
                active.append(meta)
                selected_versions.add(version)
    return active, older


def archive_ipa(ipa_dir: Path, filename: str) -> Path:
    """Move one inactive IPA within its own volume. Never overwrite an archive."""
    if _name_is_protected(filename):
        raise OSError("protected IPA cannot be archived")
    source = resolve_ipa(ipa_dir, filename)
    if source is None:
        raise OSError("IPA not found or ambiguous")
    if is_protected_path(source):
        raise OSError("protected IPA cannot be archived")
    archive_dir = source.parent / ".archive"
    if source.is_symlink() or not source.is_file():
        raise OSError(f"not a regular IPA file: {source}")
    if archive_dir.is_symlink():
        raise OSError(f"archive directory is a symlink: {archive_dir}")
    archive_dir.mkdir(exist_ok=True)
    destination = archive_dir / filename
    suffix = 1
    while destination.exists() or destination.is_symlink():
        destination = archive_dir / f"{source.stem}__{suffix}{source.suffix}"
        suffix += 1
    source.rename(destination)
    return destination
