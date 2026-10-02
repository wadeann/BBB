#!/usr/bin/env python3
"""Generate fail-closed provenance audits for the historical research dataset.

This script never downloads, synthesizes, interpolates, or mutates production history.
It only compares existing independent source artifacts against production tables and
writes detailed gap reports for human review.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from a_share_agent.backtest.data_integrity import verify_coverage_binding, verify_raw_dataset_manifest
from a_share_agent.backtest.provenance_audit import (
    audit_interval_provenance,
    classify_universe_difference,
    is_a_share_common_equity_symbol,
    local_record_active_on,
    read_csv_rows,
    reconcile_corporate_action_sets,
    write_source_audit_csv,
)

ROOT = Path(__file__).resolve().parent.parent
BACKTEST = ROOT / "data" / "backtest"
SNAPSHOT_DIR = BACKTEST / "official_universe_snapshots"
MASTER = BACKTEST / "security_master.csv"
STATUS = BACKTEST / "historical_status_intervals.csv"
SECTOR = BACKTEST / "historical_sector_intervals.csv"
STATUS_PROVENANCE = BACKTEST / "status_provenance.csv"
SECTOR_PROVENANCE = BACKTEST / "sector_provenance.csv"
PROD_CA = BACKTEST / "corporate_actions.csv"
OFFICIAL_CA = BACKTEST / "official_corporate_actions_register.csv"
OFFICIAL_CA_MANIFEST = BACKTEST / "official_corporate_actions_manifest.json"
RAW_MANIFEST = ROOT / "raw_dataset_manifest.json"
COVERAGE = ROOT / "daily_raw_coverage.csv"
COVERAGE_MANIFEST = ROOT / "daily_raw_coverage_manifest.json"

UNIVERSE_DIFF = ROOT / "official_universe_set_diff_detailed.csv"
STATUS_AUDIT = ROOT / "status_provenance_audit.csv"
SECTOR_AUDIT = ROOT / "sector_provenance_audit.csv"
CA_DIFF = ROOT / "corporate_action_set_diff.csv"
AUDIT_JSON = ROOT / "historical_data_provenance_audit.json"
PROGRESS_MD = ROOT / "HISTORICAL_DATA_BUILD_PROGRESS.md"


def _local_rows_by_symbol() -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv_rows(MASTER):
        symbol = str(row.get("symbol") or "").strip()
        board = str(row.get("board") or "").strip()
        if symbol and is_a_share_common_equity_symbol(symbol, board):
            out[symbol].append(row)
    return dict(out)


def _asset_audit_map() -> dict[str, str]:
    path = ROOT / "official_universe_asset_type_audit.csv"
    result: dict[str, str] = {}
    for row in read_csv_rows(path):
        symbol = str(row.get("symbol") or "").strip()
        if symbol and str(row.get("included") or "").lower() not in {"true", "1"}:
            result[symbol] = str(row.get("security_type") or "OTHER_SECURITY")
    return result


def audit_universe() -> dict[str, Any]:
    local = _local_rows_by_symbol()
    excluded = _asset_audit_map()
    rows: list[dict[str, str]] = []
    snapshot_count = 0
    missing_total = 0
    extra_total = 0

    for snap in sorted(SNAPSHOT_DIR.glob("20??-??-??.csv")):
        date = snap.stem
        official_rows = {
            str(r.get("symbol") or "").strip(): r
            for r in read_csv_rows(snap)
            if str(r.get("security_type") or "A_SHARE_COMMON_EQUITY").upper() == "A_SHARE_COMMON_EQUITY"
            and str(r.get("symbol") or "").strip()
        }
        local_active: dict[str, dict[str, str]] = {}
        for symbol, candidates in local.items():
            for candidate in candidates:
                if local_record_active_on(candidate, date):
                    local_active[symbol] = candidate
                    break
        official_set = set(official_rows)
        local_set = set(local_active)
        missing = sorted(official_set - local_set)
        extra = sorted(local_set - official_set)
        missing_total += len(missing)
        extra_total += len(extra)
        snapshot_count += 1

        for difference_type, symbols in (("MISSING_IN_LOCAL", missing), ("EXTRA_IN_LOCAL", extra)):
            for symbol in symbols:
                official = official_rows.get(symbol)
                local_candidates = local.get(symbol, [])
                representative = local_candidates[0] if local_candidates else {}
                root_cause = classify_universe_difference(
                    difference_type=difference_type,
                    audit_date=date,
                    official_row=official,
                    local_rows=local_candidates,
                    excluded_security_type=excluded.get(symbol),
                )
                required_action = {
                    "LOCAL_MASTER_MISSING": "verify authoritative listing record, then add only if truly A-share common equity",
                    "LOCAL_LISTING_DATE_MISMATCH": "verify and correct listing_date/active_from from exchange record",
                    "LOCAL_DELISTING_DATE_MISMATCH": "verify and correct delisting_date/active_to from exchange record",
                    "LOCAL_PRELISTING_LEAKAGE": "correct active_from/listing_date; do not delete the security",
                    "LOCAL_POST_DELISTING_LEAKAGE": "correct active_to/delisting_date; preserve historical pre-delisting membership",
                    "OFFICIAL_SOURCE_NON_A_SHARE": "exclude from A_SHARE_COMMON_EQUITY official snapshot; do not add to local master",
                    "SNAPSHOT_DATE_SEMANTICS": "review exchange snapshot date semantics and local interval boundary",
                    "OFFICIAL_SNAPSHOT_MISSING_OR_DATE_SEMANTICS": "review official register completeness/date semantics before changing local master",
                }.get(root_cause, "manual source-level review required")
                rows.append(
                    {
                        "audit_date": date,
                        "symbol": symbol,
                        "company_name": str((official or {}).get("name") or representative.get("name") or ""),
                        "exchange": str((official or {}).get("exchange") or ""),
                        "board": str((official or {}).get("board") or representative.get("board") or ""),
                        "difference_type": difference_type,
                        "security_type": str((official or {}).get("security_type") or excluded.get(symbol) or "A_SHARE_COMMON_EQUITY"),
                        "listing_date": str((official or {}).get("listing_date") or representative.get("listing_date") or ""),
                        "delisting_date": str(representative.get("delisting_date") or ""),
                        "local_active_from": str(representative.get("active_from") or ""),
                        "local_active_to": str(representative.get("active_to") or ""),
                        "official_source": str((official or {}).get("source") or ""),
                        "official_document_id_or_url": str((official or {}).get("source_document_id_or_url") or ""),
                        "root_cause": root_cause,
                        "required_action": required_action,
                    }
                )

    fields = [
        "audit_date", "symbol", "company_name", "exchange", "board", "difference_type", "security_type",
        "listing_date", "delisting_date", "local_active_from", "local_active_to", "official_source",
        "official_document_id_or_url", "root_cause", "required_action",
    ]
    with UNIVERSE_DIFF.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return {
        "snapshot_count": snapshot_count,
        "missing_total": missing_total,
        "extra_total": extra_total,
        "match": bool(snapshot_count and missing_total == 0 and extra_total == 0),
        "detailed_diff_file": UNIVERSE_DIFF.name,
        "root_cause_counts": dict(sorted(__import__("collections").Counter(r["root_cause"] for r in rows).items())),
    }


def write_ca_diff(metrics: dict[str, Any]) -> None:
    fields = ["status", "symbol", "action_type", "ex_date", "record_date", "detail"]
    rows: list[dict[str, str]] = []
    if not metrics.get("official_register_valid"):
        rows.append({"status": "OFFICIAL_REGISTER_UNAVAILABLE_OR_UNVERIFIED", "detail": str((metrics.get("official_register_validation") or {}).get("reason") or "unknown")})
    else:
        for status, key_name in (
            ("MISSING_IN_PRODUCTION", "missing_keys"),
            ("EXTRA_IN_PRODUCTION", "extra_keys"),
            ("VALUE_CONFLICT", "conflicting_keys"),
        ):
            for key in metrics.get(key_name) or []:
                rows.append({"status": status, "symbol": key[0], "action_type": key[1], "ex_date": key[2], "record_date": key[3], "detail": "independent set reconciliation"})
    with CA_DIFF.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build_progress_markdown(audit: dict[str, Any]) -> str:
    u = audit["universe"]
    raw = audit["raw_dataset"]
    cov = audit["coverage_binding"]
    ca = audit["corporate_actions"]
    st = audit["status"]
    se = audit["sector"]
    blockers = []
    if not u["match"]: blockers.append(f"Universe unresolved differences: missing={u['missing_total']}, extra={u['extra_total']}")
    if not raw.get("raw_dataset_hash_match"): blockers.append("Raw dataset fingerprint mismatch or dataset unmounted")
    if not cov.get("daily_raw_coverage_fresh"): blockers.append("Daily Raw coverage artifact is stale/unbound")
    if not ca.get("complete"): blockers.append("Independent Corporate Action event set incomplete/unverified")
    if not st.get("dataset_complete"): blockers.append(f"Status provenance coverage={st.get('source_coverage', 0):.2%}")
    if not se.get("dataset_complete"): blockers.append(f"Sector provenance coverage={se.get('source_coverage', 0):.2%}")
    lines = [
        "# Historical Data Build Progress",
        "",
        f"Generated: {audit['generated_at']}",
        "",
        "## Universe",
        f"- Snapshots audited: {u['snapshot_count']}",
        f"- Missing in local: {u['missing_total']}",
        f"- Extra in local: {u['extra_total']}",
        f"- Exact set match: {u['match']}",
        f"- Detailed diff: `{u['detailed_diff_file']}`",
        "",
        "## Raw OHLCV",
        f"- Mounted: {raw.get('mounted')}",
        f"- Hash match: {raw.get('raw_dataset_hash_match')}",
        f"- Actual files: {raw.get('actual_file_count')}",
        f"- Actual rows: {raw.get('actual_row_count')}",
        f"- Dataset hash: `{raw.get('actual_raw_dataset_hash')}`",
        f"- Daily coverage fresh: {cov.get('daily_raw_coverage_fresh')}",
        "",
        "## Corporate Actions",
        f"- Official register valid: {ca.get('official_register_valid')}",
        f"- Official events: {ca.get('expected_events')}",
        f"- Production events: {ca.get('loaded_events')}",
        f"- Missing: {ca.get('missing_events')}",
        f"- Extra: {ca.get('extra_events')}",
        f"- Conflicts: {ca.get('conflicting_events')}",
        f"- Dataset complete: {ca.get('complete')}",
        "",
        "## Historical Status",
        f"- Intervals: {st.get('interval_count')}",
        f"- Verified intervals: {st.get('verified_interval_count')}",
        f"- Source coverage: {st.get('source_coverage', 0):.2%}",
        f"- Dataset complete: {st.get('dataset_complete')}",
        "",
        "## Historical Sector",
        f"- Intervals: {se.get('interval_count')}",
        f"- Verified intervals: {se.get('verified_interval_count')}",
        f"- Source coverage: {se.get('source_coverage', 0):.2%}",
        f"- Dataset complete: {se.get('dataset_complete')}",
        "",
        "## Remaining blockers",
    ]
    lines.extend([f"- {x}" for x in blockers] or ["- None from provenance audit (Preflight still determines formal readiness)."])
    lines += [
        "",
        "## Safety rule",
        "This audit never makes `formal_full_market_ready` true by itself. Formal readiness must be produced by the normal Preflight after all real datasets and provenance gates pass.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    universe = audit_universe()
    status = audit_interval_provenance(STATUS, STATUS_PROVENANCE)
    sector = audit_interval_provenance(SECTOR, SECTOR_PROVENANCE)
    write_source_audit_csv(STATUS_AUDIT, status)
    write_source_audit_csv(SECTOR_AUDIT, sector)
    ca = reconcile_corporate_action_sets(PROD_CA, OFFICIAL_CA, OFFICIAL_CA_MANIFEST)
    write_ca_diff(ca)
    raw = verify_raw_dataset_manifest(BACKTEST / "raw_prices", RAW_MANIFEST)
    coverage = verify_coverage_binding(COVERAGE, COVERAGE_MANIFEST, raw.get("actual_raw_dataset_hash"))
    audit = {
        "audit_version": "0.7.6",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe": universe,
        "raw_dataset": raw,
        "coverage_binding": coverage,
        "corporate_actions": ca,
        "status": status,
        "sector": sector,
    }
    AUDIT_JSON.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    PROGRESS_MD.write_text(build_progress_markdown(audit), encoding="utf-8")
    print(json.dumps(audit, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
