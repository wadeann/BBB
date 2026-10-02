#!/usr/bin/env python3
"""Refresh only Universe reconciliation artifacts after a reviewed master correction.

This script intentionally does not re-audit Raw OHLCV, Corporate Actions, Status or
Sector data. It reuses their last committed audit values and marks the refresh scope
explicitly so a GitHub runner without the external Raw dataset cannot overwrite those
sections with false negatives.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from audit_historical_data_provenance import (
    AUDIT_JSON,
    PROGRESS_MD,
    ROOT,
    audit_universe,
    build_progress_markdown,
)
from a_share_agent.backtest.universe_reconciliation import reconcile_universe_snapshot_counts


def _patch_latest_preflight(universe: dict) -> None:
    path = ROOT / "latest_research_preflight.json"
    if not path.exists():
        return
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return

    # CLI output is normally wrapped as {report_type, generated_at, ok, payload:{...}},
    # while older fixtures may be a bare payload. Patch the actual payload in either
    # representation and keep wrapper metadata intact.
    payload = document.get("payload") if isinstance(document, dict) else None
    if not isinstance(payload, dict):
        payload = document
    if not isinstance(payload, dict):
        raise RuntimeError("latest_research_preflight.json is not an object/payload wrapper")

    payload["official_universe_set_match"] = bool(universe["match"])
    payload["universe_missing_symbol_count"] = int(universe["missing_total"])
    payload["universe_extra_symbol_count"] = int(universe["extra_total"])
    payload["universe_unique_missing_symbol_count"] = int(universe.get("unique_missing_symbol_count", 0))
    payload["universe_unique_extra_symbol_count"] = int(universe.get("unique_extra_symbol_count", 0))
    payload["universe_reconciliation"] = universe

    checklist = dict(payload.get("criteria_checklist") or {})
    checklist["12_official_universe_set_match"] = bool(universe["match"])
    payload["criteria_checklist"] = checklist
    # The overall gate remains computed from all criteria. Never promote readiness
    # from a partial Universe-only artifact refresh.
    payload["formal_full_market_ready"] = bool(checklist and all(checklist.values()))
    payload["research_grade_candidate"] = payload["formal_full_market_ready"]
    payload["universe_reconciliation_refreshed_at"] = datetime.now(timezone.utc).isoformat()
    payload["partial_refresh_scope"] = "UNIVERSE_ONLY_OTHER_PREFLIGHT_FIELDS_CARRIED_FORWARD"

    path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    if not AUDIT_JSON.exists():
        raise RuntimeError(f"missing committed provenance artifact: {AUDIT_JSON}")
    audit = json.loads(AUDIT_JSON.read_text(encoding="utf-8"))
    universe = audit_universe()  # also rewrites detailed Universe diff

    shared = reconcile_universe_snapshot_counts(
        ROOT / "data" / "backtest" / "security_master.csv",
        ROOT / "data" / "backtest" / "official_universe_snapshots",
    )
    # Keep the detailed/root-cause audit but copy the shared unique-symbol semantics.
    for key in (
        "missing_observation_count",
        "extra_observation_count",
        "unique_missing_symbol_count",
        "unique_extra_symbol_count",
        "unique_missing_symbols",
        "unique_extra_symbols",
        "per_snapshot",
    ):
        universe[key] = shared[key]
    if universe["missing_total"] != shared["missing_total"] or universe["extra_total"] != shared["extra_total"]:
        raise RuntimeError("Universe detailed audit and shared reconciliation diverged")

    audit["universe"] = universe
    audit["universe_refreshed_at"] = datetime.now(timezone.utc).isoformat()
    audit["refresh_scope"] = "UNIVERSE_ONLY_OTHER_SECTIONS_CARRIED_FORWARD"
    AUDIT_JSON.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    PROGRESS_MD.write_text(build_progress_markdown(audit), encoding="utf-8")
    _patch_latest_preflight(universe)

    print(json.dumps(universe, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
