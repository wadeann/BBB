#!/usr/bin/env python3
"""Generate raw_dataset_manifest.json with cryptographic fingerprint of external Raw OHLCV dataset.

Manifest contains:
- dataset_id
- dataset_version
- date_range
- file_count
- row_count
- per-file SHA256
- overall_dataset_hash (Merkle/tree hash)
- packaging mount state (reflecting that clean_code.zip does NOT include raw CSVs)
"""

import csv
import json
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "backtest" / "raw_prices"
MANIFEST_FILE = ROOT / "raw_dataset_manifest.json"


def sha256_file(filepath: Path) -> str:
    h = hashlib.sha256()
    with filepath.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def generate_raw_manifest():
    print(f"Scanning raw files in {RAW_DIR}...")
    files = sorted(RAW_DIR.glob("*.csv"))
    file_hashes = {}
    total_rows = 0

    merkle_hasher = hashlib.sha256()

    for f in files:
        h = sha256_file(f)
        file_hashes[f.name] = h
        merkle_hasher.update(f"{f.name}:{h}\n".encode("utf-8"))
        try:
            with f.open("r", encoding="utf-8-sig") as fh:
                total_rows += sum(1 for _ in fh) - 1
        except Exception:
            pass

    overall_hash = merkle_hasher.hexdigest()

    manifest = {
        "dataset_id": "ashare_raw_ohlcv_v0.7.4",
        "dataset_version": "0.7.4",
        "generated_at": "2026-10-02T17:15:00+08:00",
        "date_range": {
            "start": "2024-10-01",
            "end": "2026-09-30"
        },
        "summary": {
            "file_count": len(files),
            "row_count": total_rows,
            "overall_dataset_hash": overall_hash
        },
        "packaging": {
            "clean_code_zip_includes_raw_data": False,
            "external_mount_path": "data/backtest/raw_prices/",
            "mounted": False,
            "verification_rule": "If external raw dataset is mounted, its overall_dataset_hash must match manifest. If unmounted or mismatch, Preflight FAIL."
        },
        "file_hashes": file_hashes
    }

    with MANIFEST_FILE.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    print(f"Generated {MANIFEST_FILE}: {len(files)} files, {total_rows} rows, overall_hash={overall_hash[:16]}...")
    return manifest


if __name__ == "__main__":
    generate_raw_manifest()
