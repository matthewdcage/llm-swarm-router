"""Hub torrent chip → Jackett indexer selection from catalog categories."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DEFAULT_CATALOG = Path(__file__).with_name("torrent-indexers-catalog.json")
_VALID_HUB_CATEGORIES = frozenset({"all", "tv", "movies", "games"})


def normalize_hub_category(raw: str | None) -> str:
    cat = (raw or "all").strip().lower()
    if cat not in _VALID_HUB_CATEGORIES:
        return "all"
    return cat


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    p = path or _DEFAULT_CATALOG
    if not p.is_file():
        return {"indexers": []}
    return json.loads(p.read_text(encoding="utf-8"))


def catalog_by_jackett_id(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in catalog.get("indexers") or []:
        if not isinstance(row, dict):
            continue
        iid = str(row.get("jackett_id") or "").strip()
        if iid:
            out[iid] = row
    return out


def indexer_matches_hub_category(
    categories: list[str] | None,
    hub_category: str,
) -> bool:
    hub = normalize_hub_category(hub_category)
    if hub == "all":
        return True
    tags = {str(c).strip().lower() for c in (categories or []) if c}
    if not tags:
        tags = {"general"}
    if hub == "tv":
        return bool(tags & {"tv", "anime", "general"})
    if hub == "movies":
        return bool(tags & {"movies", "general"})
    if hub == "games":
        return "games" in tags
    return True


def resolve_indexer_ids_for_search(
    hub_category: str,
    configured_ids: list[str],
    catalog: dict[str, Any],
) -> list[str]:
    """Keep configured order; filter by catalog categories (unknown → general)."""
    hub = normalize_hub_category(hub_category)
    if hub == "all":
        return list(configured_ids)
    by_id = catalog_by_jackett_id(catalog)
    matched: list[str] = []
    for iid in configured_ids:
        row = by_id.get(iid) or {}
        cats = row.get("categories")
        if not isinstance(cats, list):
            cats = ["general"]
        if indexer_matches_hub_category(cats, hub):
            matched.append(iid)
    return matched
