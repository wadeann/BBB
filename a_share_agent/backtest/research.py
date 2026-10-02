"""v0.7.5 fail-closed research preflight wrapper.

The v0.7.4 research implementation is retained in ``research_legacy`` to keep this
release patch small and reviewable. This module tightens provenance gates around
that implementation and monkey-patches its ResearchLab global preflight reference,
so every full-market suite passes through the hardened checks.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from . import research_legacy as _legacy
from .data import HistoricalDataProvider
from .data_integrity import sha256_file, verify_coverage_binding, verify_raw_dataset_manifest
from .service import settings_from

ResearchLab = _legacy.ResearchLab
research_validity = _legacy.research_validity
_metric_row = _legacy._metric_row
_neutral_sector_share = _legacy._neutral_sector_share
_ORIGINAL_PREFLIGHT = _legacy.run_research_preflight


def _is_a_share_common_equity_symbol(symbol: str, board: str) -> bool:
    code = symbol.split(".", 1)[0].zfill(6)
    board = board.upper()
    if board == "SSE_MAIN": return code.startswith(("600", "601", "603", "605"))
    if board == "STAR": return code.startswith("688")
    if board == "SZSE_MAIN": return code.startswith(("000", "001", "002", "003"))
    if board == "CHINEXT": return code.startswith(("300", "301"))
    if board == "BSE": return code.startswith(("43", "83", "87", "88", "92"))
    return False


def _official_universe_audit(config, mcp, overrides: dict[str, Any] | None) -> tuple[bool, int, int]:
    settings = settings_from(config, overrides or {})
    provider = HistoricalDataProvider(config.project_root, mcp, use_cache=settings.cache)
    universe = provider.load_universe_for_period(settings.start_date, settings.end_date, settings.universe_file,
        max_universe=settings.max_universe, mode=settings.universe_mode)
    snapshot_dir = config.project_root / "data" / "backtest" / "official_universe_snapshots"
    snapshots = sorted(snapshot_dir.glob("*.csv")) if snapshot_dir.exists() else []
    if not snapshots: return False, 0, 0
    extra = missing = checked = 0
    for snap in snapshots:
        date = snap.stem
        if len(date) != 10 or not date.startswith("20"): continue
        official: set[str] = set()
        with snap.open("r", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                symbol = str(row.get("symbol") or "").strip()
                board = str(row.get("board") or "").strip().upper()
                security_type = str(row.get("security_type") or "").strip().upper()
                if security_type:
                    if security_type != "A_SHARE_COMMON_EQUITY": continue
                elif not _is_a_share_common_equity_symbol(symbol, board):
                    continue
                if symbol: official.add(symbol)
        active = set(provider.active_symbols_on(date, universe.symbols))
        extra += len(active - official); missing += len(official - active); checked += 1
    return bool(checked and extra == 0 and missing == 0), extra, missing


def _read_ca_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists(): return []
    with path.open("r", encoding="utf-8-sig") as fh: return [dict(r) for r in csv.DictReader(fh)]


def _ca_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return tuple(str(row.get(k) or "").strip() for k in ("symbol", "action_type", "ex_date", "record_date"))  # type: ignore[return-value]


def _ca_values(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(str(row.get(k) or "").strip() for k in ("cash_dividend_per_share", "bonus_ratio", "stock_dividend_ratio", "split_ratio", "rights_ratio", "rights_price"))


def _corporate_action_audit(root: Path) -> dict[str, Any]:
    prod_path = root / "data" / "backtest" / "corporate_actions.csv"
    reg_path = root / "data" / "backtest" / "official_corporate_actions_register.csv"
    manifest_path = root / "data" / "backtest" / "official_corporate_actions_manifest.json"
    manifest: dict[str, Any] = {}
    try:
        if manifest_path.exists(): manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception: manifest = {}
    register_valid = bool(reg_path.exists() and manifest.get("source_type") == "INDEPENDENT_OFFICIAL_EXPORT"
        and manifest.get("source_dataset_id") and manifest.get("register_sha256")
        and manifest.get("register_sha256") == sha256_file(reg_path))
    prod = _read_ca_rows(prod_path); official = _read_ca_rows(reg_path) if register_valid else []
    prod_map = {_ca_key(r): r for r in prod if all(_ca_key(r))}; off_map = {_ca_key(r): r for r in official if all(_ca_key(r))}
    missing = set(off_map) - set(prod_map); extra = set(prod_map) - set(off_map); common = set(off_map) & set(prod_map)
    conflicts = {k for k in common if _ca_values(off_map[k]) != _ca_values(prod_map[k])}
    synthetic = sum(1 for r in prod if "SYNTHETIC" in str(r.get("source") or "").upper() or "SYNTHETIC" in str(r.get("source_url_or_document_id") or "").upper())
    complete = bool(register_valid and off_map and not missing and not extra and not conflicts and not synthetic and len(prod_map) == len(off_map))
    return {"official_register_valid": register_valid, "official_source_dataset_id": manifest.get("source_dataset_id") if register_valid else None,
        "expected_events": len(off_map), "loaded_events": len(prod_map), "matched_events": len(common)-len(conflicts), "missing_events": len(missing),
        "extra_events": len(extra), "conflicting_events": len(conflicts), "synthetic_events": synthetic,
        "ratio": round((len(common)-len(conflicts))/len(off_map), 4) if off_map else 0.0, "complete": complete}


def _daily_coverage_audit(root: Path, actual_raw_hash: str | None) -> dict[str, Any]:
    csv_path = root / "daily_raw_coverage.csv"; manifest_path = root / "daily_raw_coverage_manifest.json"
    binding = verify_coverage_binding(csv_path, manifest_path, actual_raw_hash)
    values: list[float] = []; board_values = {k: [] for k in ("SSE_MAIN", "STAR", "SZSE_MAIN", "CHINEXT", "BSE")}
    colmap = {"SSE_MAIN":"sse_main_cov", "STAR":"star_cov", "SZSE_MAIN":"szse_main_cov", "CHINEXT":"chinext_cov", "BSE":"bse_cov"}
    if binding.get("daily_raw_coverage_fresh") and csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                raw = str(row.get("active_raw_coverage") or row.get("raw_coverage_pct") or "").rstrip("%")
                try:
                    v=float(raw); values.append(v/100.0 if v>1 else v)
                except ValueError: continue
                for board,col in colmap.items():
                    try:
                        x=float(str(row.get(col) or "").rstrip("%")); board_values[board].append(x/100.0 if x>1 else x)
                    except ValueError: pass
    ordered=sorted(values)
    return {"binding":binding, "fresh":bool(binding.get("daily_raw_coverage_fresh")), "min":ordered[0] if ordered else 0.0,
        "median":ordered[len(ordered)//2] if ordered else 0.0, "p05":ordered[max(0,int(len(ordered)*0.05))] if ordered else 0.0,
        "days_below_98pct":sum(1 for v in values if v<0.98), "days":len(values), "by_exchange":{k:(min(v) if v else 0.0) for k,v in board_values.items()}}


def run_research_preflight(config, mcp, *, overrides: dict[str, Any] | None = None, sample_size: int = 30) -> dict[str, Any]:
    result = _ORIGINAL_PREFLIGHT(config, mcp, overrides=overrides, sample_size=sample_size); root = config.project_root
    universe_match, extra, missing = _official_universe_audit(config, mcp, overrides)
    result["official_universe_set_match"] = universe_match; result["universe_extra_symbol_count"] = extra; result["universe_missing_symbol_count"] = missing
    ca = _corporate_action_audit(root)
    result["official_corporate_action_register_valid"] = ca["official_register_valid"]; result["corporate_action_expected_vs_loaded"] = ca
    result["corporate_action_source_coverage"] = ca["ratio"]; result["corporate_action_dataset_complete"] = ca["complete"]; result["corporate_action_ready"] = ca["complete"]
    result["synthetic_corporate_actions_detected"] = ca["synthetic_events"]
    raw = verify_raw_dataset_manifest(root / "data" / "backtest" / "raw_prices", root / "raw_dataset_manifest.json")
    raw_match = bool(raw.get("raw_dataset_hash_match")); coverage = _daily_coverage_audit(root, raw.get("actual_raw_dataset_hash")); coverage_fresh = coverage["fresh"]
    result["raw_dataset_hash_match"] = raw_match; result["daily_raw_coverage_fresh"] = coverage_fresh; result["raw_dataset_integrity"] = raw
    result["daily_raw_coverage_binding"] = coverage["binding"]; result["daily_raw_bar_coverage"] = coverage["min"]; result["raw_bar_coverage_by_exchange"] = coverage["by_exchange"]
    result["daily_raw_coverage_audit"] = {"min_daily_raw_coverage":coverage["min"], "median_daily_raw_coverage":coverage["median"], "p05_daily_raw_coverage":coverage["p05"], "days_below_98pct":coverage["days_below_98pct"], "total_evaluated_days":coverage["days"]}
    raw_ready = bool(ca["complete"] and raw_match and coverage_fresh and coverage["min"]>=0.98 and coverage["days"]>0 and coverage["days_below_98pct"]==0 and all(v>=0.98 for v in coverage["by_exchange"].values()))
    result["raw_execution_price_ready"] = raw_ready
    checklist = dict(result.get("criteria_checklist") or {})
    checklist.update({"8_corporate_action_dataset_complete":ca["complete"], "9_raw_execution_price_ready":raw_ready,
        "10_daily_raw_bar_coverage":bool(coverage["min"]>=0.98 and coverage["days"]>0 and coverage["days_below_98pct"]==0),
        "11_each_exchange_raw_coverage":bool(coverage["by_exchange"] and all(v>=0.98 for v in coverage["by_exchange"].values())),
        "12_official_universe_set_match":universe_match, "16_raw_dataset_hash_match":raw_match, "17_daily_raw_coverage_fresh":coverage_fresh})
    result["criteria_checklist"] = checklist; formal = bool(checklist and all(checklist.values())); result["formal_full_market_ready"] = formal; result["research_grade_candidate"] = formal
    warnings=list(result.get("provider_warnings") or [])
    if not ca["official_register_valid"]: warnings.append("CORPORATE_ACTION_OFFICIAL_REGISTER_UNAVAILABLE_OR_UNVERIFIED")
    if not raw_match: warnings.append("RAW_DATASET_HASH_MISMATCH_OR_UNMOUNTED")
    if not coverage_fresh: warnings.append("DAILY_RAW_COVERAGE_STALE_OR_UNBOUND")
    result["provider_warnings"] = list(dict.fromkeys(warnings)); return result


_legacy.run_research_preflight = run_research_preflight
