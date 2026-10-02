#!/usr/bin/env python3
"""Generate a cryptographic fingerprint for the external Raw OHLCV dataset."""
import json
from datetime import datetime, timezone
from pathlib import Path

from a_share_agent.backtest.data_integrity import raw_dataset_fingerprint
from a_share_agent.git_utils import get_git_metadata

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "backtest" / "raw_prices"
MANIFEST_FILE = ROOT / "raw_dataset_manifest.json"


def generate_raw_manifest():
    fp = raw_dataset_fingerprint(RAW_DIR)
    git_meta = get_git_metadata(ROOT)
    manifest = {
        "dataset_id": "ashare_raw_ohlcv_external",
        "dataset_version": "0.7.5",
        "producer_git_commit": git_meta.get("git_commit_sha"),
        "producer_code_version": git_meta.get("producer_code_version"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date_range": {"start": "2024-10-01", "end": "2026-09-30"},
        "summary": {
            "file_count": fp["file_count"],
            "row_count": fp["row_count"],
            "overall_dataset_hash": fp["overall_dataset_hash"],
        },
        "packaging": {
            "clean_code_zip_includes_raw_data": False,
            "external_mount_path": "data/backtest/raw_prices/",
            "mounted_at_generation": fp["mounted"],
            "verification_rule": "Preflight recomputes current file hashes and fails on any file/count/hash mismatch.",
        },
        "file_hashes": fp["file_hashes"],
    }
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Generated {MANIFEST_FILE}: {fp['file_count']} files, {fp['row_count']} rows, {fp['overall_dataset_hash'][:16]}...")
    return manifest


if __name__ == "__main__":
    generate_raw_manifest()
