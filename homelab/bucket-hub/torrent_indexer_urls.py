"""Torrent indexer mirror URL validation and merge helpers."""
from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlparse

_PRIVATE_NETS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def normalize_indexer_url(url: str) -> str:
    """Jackett-style site link: https host path, trailing slash."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw.rstrip("/") + ("/" if not raw.endswith("/") else "")
    path = parsed.path or "/"
    if not path.endswith("/"):
        path += "/"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{port}{path}"


def _host_is_blocked(host: str) -> bool:
    host = (host or "").strip().lower()
    if not host:
        return True
    if host in ("localhost", "localhost.localdomain"):
        return True
    if re.match(r"^127\.\d+\.\d+\.\d+$", host):
        return True
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return any(addr in net for net in _PRIVATE_NETS)


def validate_public_http_url(url: str) -> str:
    """Return normalized URL or raise ValueError."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("URL is required")
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError("URL must use http or https")
    if not parsed.netloc:
        raise ValueError("URL must include a host")
    host = parsed.hostname or ""
    if _host_is_blocked(host):
        raise ValueError("URL must not point at a private or local address")
    return normalize_indexer_url(raw)


def urls_match(a: str, b: str) -> bool:
    return normalize_indexer_url(a) == normalize_indexer_url(b)


def parse_reference_url_entries(raw_list: list[Any] | None) -> list[tuple[str, str]]:
    """Catalog reference_urls: plain strings or {url, label} objects."""
    out: list[tuple[str, str]] = []
    for item in raw_list or []:
        if isinstance(item, str):
            out.append((item, "Megathread hint"))
        elif isinstance(item, dict) and item.get("url"):
            out.append((str(item["url"]), str(item.get("label") or "Megathread hint")))
    return out


def mirror_urls_to_check(
    mirror_candidates: list[dict[str, Any]],
    *,
    include_references: bool,
) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for row in mirror_candidates:
        if not isinstance(row, dict) or not row.get("url"):
            continue
        if not include_references and not row.get("editable"):
            continue
        try:
            norm = normalize_indexer_url(str(row["url"]))
        except Exception:
            norm = str(row["url"])
        if norm in seen:
            continue
        seen.add(norm)
        urls.append(norm)
    return urls


def _empty_candidates_store() -> dict[str, Any]:
    return {"version": 1, "by_jackett_id": {}}


def load_candidates_store(data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        return _empty_candidates_store()
    out = _empty_candidates_store()
    by_id = data.get("by_jackett_id")
    if not isinstance(by_id, dict):
        return out
    for iid, block in by_id.items():
        if not iid or not isinstance(block, dict):
            continue
        cands = block.get("candidates")
        if not isinstance(cands, list):
            continue
        cleaned: list[dict[str, Any]] = []
        for row in cands:
            if not isinstance(row, dict):
                continue
            try:
                norm = validate_public_http_url(str(row.get("url") or ""))
            except ValueError:
                continue
            cleaned.append(
                {
                    "url": norm,
                    "label": str(row.get("label") or "")[:120],
                    "source": str(row.get("source") or "manual")[:32],
                    "added_at": row.get("added_at"),
                }
            )
        if cleaned:
            out["by_jackett_id"][str(iid)] = {"candidates": cleaned}
    return out


def merge_indexer_candidates(
    indexer_row: dict[str, Any],
    user_candidates: list[dict[str, Any]],
    reference_urls: list[Any] | None,
    checks_by_url: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build unified candidate list: reference hints + user entries + check metadata."""
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []

    def add(url: str, source: str, label: str = "", editable: bool = False) -> None:
        try:
            norm = validate_public_http_url(url)
        except ValueError:
            return
        if norm in seen:
            return
        seen.add(norm)
        check = checks_by_url.get(norm) or checks_by_url.get(url) or {}
        merged.append(
            {
                "url": norm,
                "label": label,
                "source": source,
                "editable": editable,
                "check": check or None,
            }
        )

    for ref_url, ref_label in parse_reference_url_entries(reference_urls):
        add(ref_url, "megathread", label=ref_label, editable=False)

    for row in user_candidates or []:
        if not isinstance(row, dict):
            continue
        add(
            str(row.get("url") or ""),
            str(row.get("source") or "manual"),
            label=str(row.get("label") or ""),
            editable=True,
        )

    return merged


def attach_candidates_to_indexers(
    merged_indexers: list[dict[str, Any]],
    candidates_store: dict[str, Any],
    candidates_status: dict[str, Any],
    catalog_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add `mirror_candidates` to each indexer row."""
    by_id_store = candidates_store.get("by_jackett_id") or {}
    status_by_id = candidates_status.get("by_jackett_id") or {}
    out: list[dict[str, Any]] = []
    for row in merged_indexers:
        if not isinstance(row, dict):
            continue
        iid = str(row.get("jackett_id") or "")
        block = by_id_store.get(iid) if isinstance(by_id_store, dict) else None
        user_cands = (block or {}).get("candidates") if isinstance(block, dict) else []
        st_block = status_by_id.get(iid) if isinstance(status_by_id, dict) else {}
        checks_list = (st_block or {}).get("checks") if isinstance(st_block, dict) else []
        checks_by_url: dict[str, dict[str, Any]] = {}
        for chk in checks_list or []:
            if not isinstance(chk, dict) or not chk.get("url"):
                continue
            try:
                norm = normalize_indexer_url(str(chk["url"]))
            except Exception:
                norm = str(chk["url"])
            checks_by_url[norm] = chk
        cat = catalog_by_id.get(iid) or {}
        refs = cat.get("reference_urls")
        if not isinstance(refs, list):
            refs = []
        new_row = dict(row)
        new_row["mirror_candidates"] = merge_indexer_candidates(
            row,
            user_cands if isinstance(user_cands, list) else [],
            refs,
            checks_by_url,
        )
        new_row["candidates_checked_at"] = (st_block or {}).get("at")
        out.append(new_row)
    return out
