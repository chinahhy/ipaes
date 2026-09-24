"""Keep three distinct installed versions per Bundle ID; archive older IPA files."""

import re
from collections import defaultdict
from pathlib import Path


KEEP_VERSIONS = 3
PROTECTED_FILES = {"X_10.76_证书安装登录版本.ipa"}


def version_order(version):
    numbers = [int(part) for part in re.findall(r"\d+", str(version or ""))]
    return tuple((numbers + [0] * 8)[:8])


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
        protected = [m for m in group if m["ipa_filename"] in PROTECTED_FILES]
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
    source = ipa_dir / filename
    archive_dir = ipa_dir / ".archive"
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
