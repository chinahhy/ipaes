import re
from typing import Optional


def filename_app_name(filename: str) -> str:
    """Return the app-like prefix from common IPA filenames."""
    stem = str(filename or "").strip()
    if stem.lower().endswith(".ipa"):
        stem = stem[:-4]
    stem = stem.strip(" ._-")
    if not stem:
        return ""

    m = re.match(r"^(.+?)[ _-]+v?\d[\d.]*\d(?:[ _.-].*)?$", stem, re.IGNORECASE)
    if m:
        name = m.group(1).strip(" ._-")
        if name:
            return name
    return stem


def _terms_for(app: dict) -> list[str]:
    raw_terms = [app.get("name", ""), *(app.get("keywords") or [])]
    terms: list[str] = []
    seen = set()
    for term in raw_terms:
        value = str(term or "").strip()
        key = value.casefold()
        if value and key not in seen:
            terms.append(value)
            seen.add(key)
    return terms


def _best_match_in(segment: str, whitelist: list, source_rank: int):
    haystack = str(segment or "").casefold()
    if not haystack:
        return None

    best = None
    for app_idx, app in enumerate(whitelist or []):
        for term in _terms_for(app):
            pos = haystack.find(term.casefold())
            if pos < 0:
                continue
            # Lower is better. Prefer: filename prefix > full filename > caption,
            # then more specific terms, then earlier occurrences, then config order.
            score = (source_rank, -len(term), pos, app_idx)
            if best is None or score < best[0]:
                best = (score, app.get("name"))
    return best


def match_whitelist(filename: str, message_text: str, whitelist: list) -> Optional[str]:
    """Return the whitelist app name that best matches this IPA candidate."""
    candidates = [
        _best_match_in(filename_app_name(filename), whitelist, 0),
        _best_match_in(filename, whitelist, 1),
        _best_match_in(message_text or "", whitelist, 2),
    ]
    candidates = [c for c in candidates if c and c[1]]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def display_app_name(filename: str, matched_app_name: str = "") -> str:
    """Prefer the IPA filename's app prefix for user-facing reports."""
    return filename_app_name(filename) or str(matched_app_name or "").strip()
