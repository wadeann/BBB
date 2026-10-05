from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def sector_resonance_filter(
    symbol: str,
    sector_code: str | None,
    sector_name: str | None,
    all_candidates: list[dict[str, Any]],
    as_of: str,
    provider: Any,
) -> dict[str, Any]:
    """Filter a candidate using sector resonance rules from ashare-picker v4.

    Evaluates sector strength by counting same-sector peers among scored
    candidates.  Returns annotation dict — all paths produce pass: True
    but categorise the strength of the sector signal.

    Parameters
    ----------
    symbol : str
        Candidate stock symbol.
    sector_code : str | None
        Industry code from security master (e.g. BK1217).  None → bypass.
    sector_name : str | None
        Human-readable sector name (for logging / debugging).
    all_candidates : list[dict[str, Any]]
        All scored candidates from the current scan.  Each dict must contain
        at least ``{"symbol": str, "score": float, "sector_code": str}``.
    as_of : str
        Date string for the scan (YYYY-MM-DD).
    provider : Any
        HistoricalDataProvider instance (not used in this implementation but
        kept in the signature for future extension e.g. fund-flow checks).

    Returns
    -------
    dict
        ``{"pass": bool, "sector_strength": int, "reason": str, "peers": list}``
    """
    if sector_code is None:
        return {"pass": True, "sector_strength": 0, "reason": "no_sector_data", "peers": []}

    # Gather same-sector peers among scored candidates
    peers: list[dict[str, Any]] = []
    for c in all_candidates:
        if c.get("sector_code") == sector_code and c.get("symbol") != symbol:
            peers.append({
                "symbol": c.get("symbol"),
                "score": c.get("score", 0),
                "strategy": c.get("strategy", ""),
            })

    strong_peers = [p for p in peers if p["score"] >= 70]
    n_strong = len(strong_peers)

    candidate_score = next(
        (c.get("score", 0) for c in all_candidates if c.get("symbol") == symbol),
        0,
    )

    if n_strong >= 3:
        return {
            "pass": True,
            "sector_strength": 3,
            "reason": "sector_strong",
            "peers": strong_peers,
        }

    if n_strong >= 1:
        return {
            "pass": True,
            "sector_strength": 2,
            "reason": "sector_moderate",
            "peers": strong_peers,
        }

    # No strong peers in same sector
    reason = "solo_strong" if candidate_score >= 80 else "solo_weak"
    return {
        "pass": True,
        "sector_strength": 1,
        "reason": reason,
        "peers": [],
    }


def build_sector_map(csv_path: str | Path) -> dict[str, list[str]]:
    """Build ``{industry_code: [symbol, ...]}`` from *security_master.csv*.

    Reads the sector CSV with the standard library ``csv`` module and groups
    symbols by their industry code.

    Parameters
    ----------
    csv_path : str | Path
        Path to the security master CSV file.

    Returns
    -------
    dict[str, list[str]]
        Mapping from industry code to list of stock symbols.
    """
    sector_map: dict[str, list[str]] = {}
    path = Path(csv_path)

    if not path.exists():
        return sector_map

    with open(path, "r", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            code = (row.get("industry_code") or "").strip()
            sym = (row.get("symbol") or "").strip()
            if code and sym:
                sector_map.setdefault(code, []).append(sym)

    return sector_map
