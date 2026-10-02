from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def raw_dataset_fingerprint(raw_dir: Path) -> dict[str, Any]:
    """Compute a deterministic fingerprint of the *currently mounted* raw CSV dataset."""
    files = sorted(raw_dir.glob("*.csv")) if raw_dir.exists() else []
    file_hashes: dict[str, str] = {}
    total_rows = 0
    tree = hashlib.sha256()
    for path in files:
        h = hashlib.sha256()
        newline_count = 0
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
                newline_count += chunk.count(b"\n")
        digest = h.hexdigest()
        file_hashes[path.name] = digest
        tree.update(f"{path.name}:{digest}\n".encode("utf-8"))
        total_rows += max(0, newline_count - 1)
    return {
        "mounted": bool(files),
        "file_count": len(files),
        "row_count": total_rows,
        "overall_dataset_hash": tree.hexdigest(),
        "file_hashes": file_hashes,
    }


def verify_raw_dataset_manifest(raw_dir: Path, manifest_path: Path) -> dict[str, Any]:
    actual = raw_dataset_fingerprint(raw_dir)
    expected: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            expected = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            expected = {}
    expected_files = expected.get("file_hashes") if isinstance(expected, dict) else {}
    if not isinstance(expected_files, dict):
        expected_files = {}
    actual_files = actual["file_hashes"]
    missing = sorted(set(expected_files) - set(actual_files))
    extra = sorted(set(actual_files) - set(expected_files))
    mismatched = sorted(
        name for name in (set(expected_files) & set(actual_files))
        if expected_files[name] != actual_files[name]
    )
    exp_summary = expected.get("summary", {}) if isinstance(expected, dict) else {}
    expected_hash = str(exp_summary.get("overall_dataset_hash") or "")
    expected_count = int(exp_summary.get("file_count") or 0)
    expected_rows = int(exp_summary.get("row_count") or 0)
    match = bool(
        expected_hash
        and actual["mounted"]
        and actual["overall_dataset_hash"] == expected_hash
        and actual["file_count"] == expected_count
        and actual["row_count"] == expected_rows
        and not missing and not extra and not mismatched
    )
    return {
        "manifest_present": manifest_path.exists(),
        "mounted": actual["mounted"],
        "expected_raw_dataset_hash": expected_hash or None,
        "actual_raw_dataset_hash": actual["overall_dataset_hash"] if actual["mounted"] else None,
        "expected_file_count": expected_count,
        "actual_file_count": actual["file_count"],
        "expected_row_count": expected_rows,
        "actual_row_count": actual["row_count"],
        "missing_files": missing,
        "extra_files": extra,
        "mismatched_files": mismatched,
        "raw_dataset_hash_match": match,
    }


def verify_coverage_binding(coverage_csv: Path, coverage_manifest: Path, actual_raw_hash: str | None) -> dict[str, Any]:
    info: dict[str, Any] = {}
    if coverage_manifest.exists():
        try:
            info = json.loads(coverage_manifest.read_text(encoding="utf-8"))
        except Exception:
            info = {}
    source_hash = str(info.get("source_raw_dataset_hash") or "")
    expected_csv_hash = str(info.get("coverage_csv_sha256") or "")
    actual_csv_hash = sha256_file(coverage_csv) if coverage_csv.exists() else ""
    valid = bool(
        actual_raw_hash
        and source_hash
        and source_hash == actual_raw_hash
        and expected_csv_hash
        and expected_csv_hash == actual_csv_hash
    )
    return {
        "coverage_manifest_present": coverage_manifest.exists(),
        "coverage_csv_present": coverage_csv.exists(),
        "source_raw_dataset_hash": source_hash or None,
        "actual_raw_dataset_hash": actual_raw_hash,
        "expected_coverage_csv_sha256": expected_csv_hash or None,
        "actual_coverage_csv_sha256": actual_csv_hash or None,
        "daily_raw_coverage_fresh": valid,
    }
