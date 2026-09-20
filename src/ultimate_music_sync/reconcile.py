from __future__ import annotations

import re
import unicodedata
from typing import Any

VERSION_MARKERS = {
    "live", "remix", "remaster", "remastered", "acoustic", "instrumental",
    "edit", "sped", "slowed", "karaoke", "demo",
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").casefold()
    return re.sub(r"[^a-z0-9]+", "", value)


def markers(value: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", value or "").casefold()))
    return words & VERSION_MARKERS


def compatible(saved: dict[str, Any], local: dict[str, Any]) -> bool:
    saved_markers = markers(saved.get("title", ""))
    local_markers = markers(local.get("title", ""))
    if saved_markers != local_markers:
        return False
    a = saved.get("duration_ms")
    b = local.get("duration_ms")
    if a and b and abs(int(a) - int(b)) > max(2000, int(a) * 0.02):
        return False
    return True


def reconcile_saved_with_library(saved_rows: list[dict[str, Any]], inventory: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_isrc: dict[str, list[dict[str, Any]]] = {}
    by_metadata: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in inventory:
        isrc = normalize(row.get("isrc", ""))
        if isrc:
            by_isrc.setdefault(isrc, []).append(row)
        by_metadata.setdefault((normalize(row.get("artist", "").split(";")[0]), normalize(row.get("title", ""))), []).append(row)

    matched: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for saved in saved_rows:
        candidate = None
        basis = None
        isrc = normalize(saved.get("isrc", ""))
        if isrc:
            for row in by_isrc.get(isrc, []):
                if compatible(saved, row):
                    candidate, basis = row, "isrc"
                    break
        if candidate is None:
            key = (normalize(saved.get("artist", "").split(",")[0]), normalize(saved.get("title", "")))
            for row in by_metadata.get(key, []):
                if compatible(saved, row):
                    candidate, basis = row, "metadata"
                    break
        if candidate:
            matched.append({**saved, "path": candidate.get("path", ""), "match_basis": basis})
        else:
            missing.append(saved)
    return {"matched": matched, "missing": missing}
