"""Torrent title parsing for Jackett results (worker + tests)."""

from __future__ import annotations

import re

_RELEASE_COMPLETE = re.compile(
    r"\b(COMPLETE|FULL\s*SERIES|SERIES\s*PACK|ALL\s*SEASONS)\b",
    re.I,
)
_RELEASE_SEASON_RANGE = re.compile(
    r"\bS(\d{1,2})\s*[-–]\s*(?:S)?(\d{1,2})\b|\bSEASON\s*(\d{1,2})\s*[-–]\s*(\d{1,2})\b",
    re.I,
)
_RELEASE_EPISODE = re.compile(r"\bS\d{1,2}E\d{1,2}\b", re.I)
_RELEASE_SEASON_PACK = re.compile(
    r"\bS\d{1,2}\b(?!\s*E\d)|\bSEASON\s*\d{1,2}\b",
    re.I,
)
_SHOW_YEAR = re.compile(r"\((\d{4})\)")
_SHOW_JUNK = re.compile(
    r"\b(\d{3,4}p|x26[45]|hevc|h\.?264|h\.?265|bluray|web-?dl|"
    r"aac|dts|remux|10bit|proper|repack|multi|dual\s*audio)\b",
    re.I,
)


def season_numbers_from_title(title: str) -> list[int]:
    """Distinct season numbers covered by a pack (empty for episodes)."""
    t = (title or "").strip()
    if not t or _RELEASE_EPISODE.search(t):
        return []
    nums: set[int] = set()
    for m in re.finditer(r"\bS(\d{1,2})\b(?!\s*E)", t, re.I):
        nums.add(int(m.group(1)))
    for m in re.finditer(r"\bSEASON\s*(\d{1,2})\b", t, re.I):
        nums.add(int(m.group(1)))
    for m in _RELEASE_SEASON_RANGE.finditer(t):
        g = m.groups()
        if g[0] and g[1]:
            a, b = int(g[0]), int(g[1])
            lo, hi = min(a, b), max(a, b)
            nums.update(range(lo, hi + 1))
        elif g[2] and g[3]:
            a, b = int(g[2]), int(g[3])
            lo, hi = min(a, b), max(a, b)
            nums.update(range(lo, hi + 1))
    return sorted(nums)


def format_season_span_label(numbers: list[int]) -> str:
    if not numbers:
        return ""
    if len(numbers) == 1:
        return f"S{numbers[0]:02d}"
    return f"S{numbers[0]:02d}–S{numbers[-1]:02d}"


def classify_release_kind(title: str) -> str:
    """Return full_show | season | episode | unknown."""
    t = (title or "").strip()
    if not t:
        return "unknown"
    if _RELEASE_EPISODE.search(t):
        return "episode"
    nums = season_numbers_from_title(t)
    if _RELEASE_COMPLETE.search(t):
        if not nums:
            return "full_show"
        if len(nums) == 1:
            return "season"
        return "full_show"
    if len(nums) >= 2:
        span = nums[-1] - nums[0]
        if span >= 5:
            return "full_show"
        return "season"
    if len(nums) == 1:
        return "season"
    if _RELEASE_SEASON_PACK.search(t):
        return "season"
    return "unknown"


def parse_season_bucket(title: str) -> str:
    """UI grouping key: full_show, s01..s99, episode, other."""
    kind = classify_release_kind(title)
    if kind == "episode":
        return "episode"
    nums = season_numbers_from_title(title)
    if kind == "full_show" and len(nums) >= 2:
        return "full_show"
    if len(nums) == 1:
        return f"s{nums[0]:02d}"
    if len(nums) >= 2:
        return f"s{nums[0]:02d}"
    return "other"


_PACK_QUERY_NOISE = re.compile(
    r"\b(complete|season|pack|series|full)\b",
    re.I,
)
_SEASON_TOKEN = re.compile(r"\bS(\d{1,2})(?!E\d)\b", re.I)
_SEASON_SHORT = re.compile(r"\bs(\d{1,2})\b", re.I)


def target_seasons_from_query(user_query: str) -> list[int]:
    """Season numbers the user asked for (e.g. ``season 1``, ``s01``), not from a release title."""
    norm = normalize_tv_search_query((user_query or "").strip())
    if not norm:
        return []
    nums: set[int] = set()
    for m in re.finditer(r"\bseason\s+(\d{1,2})\b", norm, re.I):
        nums.add(int(m.group(1)))
    return sorted(nums)


def normalize_tv_search_query(user_query: str) -> str:
    """Expand shorthand TV queries for Jackett (e.g. ``breaking bad s1``)."""
    raw = (user_query or "").strip()
    if not raw:
        return raw

    def _season_word(match: re.Match[str]) -> str:
        return f"season {int(match.group(1))}"

    expanded = _SEASON_TOKEN.sub(_season_word, raw)
    expanded = _SEASON_SHORT.sub(_season_word, expanded)
    return re.sub(r"\s+", " ", expanded).strip()


def torrent_pack_search_queries(user_query: str) -> list[str]:
    """Jackett queries for season/series packs (dual search)."""
    raw = normalize_tv_search_query((user_query or "").strip())
    if not raw:
        return []
    targets = target_seasons_from_query(user_query)
    if len(targets) == 1:
        n = targets[0]
        show = re.sub(r"\bseason\s+\d{1,2}\b", "", raw, flags=re.I)
        show = re.sub(r"\s+", " ", show).strip() or raw
        queries = [
            f"{show} season {n}",
            f"{show} s{n:02d}",
            raw,
        ]
        seen: set[str] = set()
        out: list[str] = []
        for q in queries:
            key = q.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(q)
        return out
    base = _PACK_QUERY_NOISE.sub(" ", raw)
    base = re.sub(r"\s+", " ", base).strip() or raw
    queries: list[str] = []
    if re.search(r"\bcomplete\b", raw, re.I):
        queries.append(raw)
    else:
        queries.append(f"{base} complete")
    if not re.search(r"\bseason\b", raw, re.I):
        queries.append(f"{base} season")
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out


def torrent_row_matches_target_season(row: dict, want: int) -> bool:
    """True when a pack is a single-season release for ``want`` (not S01–S05 complete)."""
    kind = str(row.get("release_kind") or "")
    if kind in ("full_show", "episode"):
        return False
    nums = row.get("season_numbers")
    if not isinstance(nums, list):
        nums = season_numbers_from_title(str(row.get("title") or ""))
    if nums == [want]:
        return True
    return False


def release_kind_rank(kind: str) -> int:
    return {
        "full_show": 4,
        "complete": 4,
        "season": 3,
        "episode": 1,
        "unknown": 0,
    }.get(kind, 0)


def guess_show_title(title: str) -> str:
    """Best-effort show/movie folder name from release title."""
    raw = (title or "").strip()
    if not raw:
        return ""
    s = raw
    s = _SHOW_YEAR.sub("", s)
    s = re.split(r"\bS\d{1,2}\b", s, maxsplit=1)[0]
    s = re.split(r"\bSEASON\s*\d", s, flags=re.I, maxsplit=1)[0]
    s = re.sub(r"\[.*?\]|\(.*?\)", " ", s)
    s = _SHOW_JUNK.sub(" ", s)
    s = re.sub(r"[._]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -.")
    if len(s) > 80:
        s = s[:80].rsplit(" ", 1)[0]
    return s or raw[:60]
