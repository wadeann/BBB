from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .data_integrity import sha256_file
from .provenance_audit import (
    is_a_share_common_equity_symbol,
    local_record_active_on,
    read_csv_rows,
)


def snapshot_row_is_target(row: dict[str, str]) -> bool:
    """Return True only for target A-share common-equity snapshot rows."""
    symbol = str(row.get("symbol") or "").strip()
    board = str(row.get("board") or "").strip().upper()
    security_type = str(row.get("security_type") or "").strip().upper()
    if not symbol or not is_a_share_common_equity_symbol(symbol, board):
        return False
    if security_type and security_type != "A_SHARE_COMMON_EQUITY":
        return False
    return True


def master_rows_by_symbol(master_rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in master_rows:
        symbol = str(row.get("symbol") or "").strip()
        board = str(row.get("board") or "").strip().upper()
        if symbol and is_a_share_common_equity_symbol(symbol, board):
            grouped[symbol].append(row)
    return dict(grouped)


def universe_sets_for_date(
    master_rows: list[dict[str, str]],
    snapshot_rows: list[dict[str, str]],
    audit_date: str,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """Build official and local target-A-share sets with identical membership semantics."""
    official_rows = {
        str(row.get("symbol") or "").strip(): row
        for row in snapshot_rows
        if snapshot_row_is_target(row)
    }

    local_active: dict[str, dict[str, str]] = {}
    for symbol, candidates in master_rows_by_symbol(master_rows).items():
        for candidate in candidates:
            if local_record_active_on(candidate, audit_date):
                local_active[symbol] = candidate
                break
    return official_rows, local_active


def _read_manifest(snapshot_dir: Path) -> dict[str, Any]:
    path = snapshot_dir / "manifest.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _audit_snapshot_integrity(snapshot_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    declared = manifest.get("snapshots") if isinstance(manifest, dict) else None
    if not isinstance(declared, dict):
        declared = {}
    disk_files = {p.stem: p for p in sorted(snapshot_dir.glob("20??-??-??.csv"))}
    declared_dates = set(str(k) for k in declared)
    disk_dates = set(disk_files)
    missing_files = sorted(declared_dates - disk_dates)
    undeclared_files = sorted(disk_dates - declared_dates)
    audits: list[dict[str, Any]] = []

    for date in sorted(disk_dates & declared_dates):
        path = disk_files[date]
        entry = declared.get(date)
        if not isinstance(entry, dict):
            entry = {}
        expected_hash = str(entry.get("sha256") or "").strip().lower()
        actual_hash = sha256_file(path)
        declared_file = str(entry.get("file") or "").strip()
        file_name_match = bool(declared_file and Path(declared_file).name == path.name)
        rows = read_csv_rows(path)
        target_count = sum(1 for row in rows if snapshot_row_is_target(row))
        try:
            expected_count = int(entry.get("total_symbols"))
        except Exception:
            expected_count = -1
        count_match = expected_count == target_count
        valid = bool(
            len(expected_hash) == 64
            and expected_hash == actual_hash
            and file_name_match
            and expected_count >= 0
            and count_match
        )
        audits.append(
            {
                "date": date,
                "file": path.name,
                "declared_file": declared_file or None,
                "expected_sha256": expected_hash or None,
                "actual_sha256": actual_hash,
                "hash_match": expected_hash == actual_hash,
                "declared_total_symbols": expected_count,
                "actual_target_symbols": target_count,
                "count_match": count_match,
                "valid": valid,
            }
        )

    ready = bool(
        declared
        and disk_files
        and not missing_files
        and not undeclared_files
        and audits
        and all(row["valid"] for row in audits)
    )
    return {
        "ready": ready,
        "declared_snapshot_count": len(declared),
        "disk_snapshot_count": len(disk_files),
        "missing_snapshot_files": missing_files,
        "undeclared_snapshot_files": undeclared_files,
        "snapshots": audits,
    }


def _audit_source_register_integrity(snapshot_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    source_registers = manifest.get("source_registers") if isinstance(manifest, dict) else None
    if not isinstance(source_registers, dict):
        source_registers = {}
    files = source_registers.get("files")
    if not isinstance(files, dict):
        files = {}
    directory = str(source_registers.get("directory") or "raw_registers").strip()
    raw_dir = snapshot_dir / "raw_registers"
    audits: list[dict[str, Any]] = []
    for name, expected in sorted(files.items()):
        filename = Path(str(name)).name
        path = raw_dir / filename
        expected_hash = str(expected or "").strip().lower()
        exists = path.is_file()
        actual_hash = sha256_file(path) if exists else None
        audits.append(
            {
                "file": filename,
                "exists": exists,
                "expected_sha256": expected_hash or None,
                "actual_sha256": actual_hash,
                "hash_match": bool(exists and expected_hash and actual_hash == expected_hash),
            }
        )
    ready = bool(files and audits and all(row["hash_match"] for row in audits))
    return {
        "ready": ready,
        "declared_directory": directory or None,
        "declared_file_count": len(files),
        "files": audits,
    }


def reconcile_universe_snapshot_counts(master_path: Path, snapshot_dir: Path) -> dict[str, Any]:
    """Reconcile hash-verified official snapshots against the local security master.

    Snapshot content is still reported for diagnostics when integrity fails, but the
    formal ``match`` flag can only be true when the manifest, every dated snapshot and
    every manifest-declared raw exchange register are byte-for-byte verified.
    """
    master_rows = read_csv_rows(master_path)
    manifest = _read_manifest(snapshot_dir)
    snapshot_integrity = _audit_snapshot_integrity(snapshot_dir, manifest)
    source_register_integrity = _audit_source_register_integrity(snapshot_dir, manifest)
    integrity_ready = bool(snapshot_integrity["ready"] and source_register_integrity["ready"])

    missing_total = 0
    extra_total = 0
    missing_unique: set[str] = set()
    extra_unique: set[str] = set()
    snapshot_count = 0
    per_snapshot: list[dict[str, Any]] = []

    for snap in sorted(snapshot_dir.glob("20??-??-??.csv")):
        date = snap.stem
        official_rows, local_active = universe_sets_for_date(master_rows, read_csv_rows(snap), date)
        official_set = set(official_rows)
        local_set = set(local_active)
        missing = sorted(official_set - local_set)
        extra = sorted(local_set - official_set)
        missing_total += len(missing)
        extra_total += len(extra)
        missing_unique.update(missing)
        extra_unique.update(extra)
        snapshot_count += 1
        per_snapshot.append(
            {
                "date": date,
                "official_count": len(official_set),
                "local_count": len(local_set),
                "missing_count": len(missing),
                "extra_count": len(extra),
                "missing_symbols": missing,
                "extra_symbols": extra,
            }
        )

    return {
        "manifest_present": bool(manifest),
        "snapshot_integrity": snapshot_integrity,
        "source_register_integrity": source_register_integrity,
        "official_snapshot_integrity_ready": integrity_ready,
        "snapshot_count": snapshot_count,
        "missing_total": missing_total,
        "extra_total": extra_total,
        "missing_observation_count": missing_total,
        "extra_observation_count": extra_total,
        "unique_missing_symbol_count": len(missing_unique),
        "unique_extra_symbol_count": len(extra_unique),
        "unique_missing_symbols": sorted(missing_unique),
        "unique_extra_symbols": sorted(extra_unique),
        "match": bool(integrity_ready and snapshot_count and missing_total == 0 and extra_total == 0),
        "set_match_ignoring_integrity": bool(snapshot_count and missing_total == 0 and extra_total == 0),
        "per_snapshot": per_snapshot,
    }
