from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_snapshot_filter():
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "audit_historical_data_provenance.py"
    spec = importlib.util.spec_from_file_location("audit_historical_data_provenance", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._snapshot_row_is_target


def test_legacy_snapshot_asset_filter_matches_preflight_rules():
    snapshot_row_is_target = _load_snapshot_filter()

    assert snapshot_row_is_target({"symbol": "600000.SH", "board": "SSE_MAIN"})
    assert snapshot_row_is_target({"symbol": "688981.SH", "board": "STAR"})
    assert snapshot_row_is_target({"symbol": "000001.SZ", "board": "SZSE_MAIN"})
    assert snapshot_row_is_target({"symbol": "300750.SZ", "board": "CHINEXT"})
    assert snapshot_row_is_target({"symbol": "920002.BJ", "board": "BSE"})

    # Legacy snapshots may not have a security_type column. These must still be excluded.
    assert not snapshot_row_is_target({"symbol": "900957.SH", "board": "SSE_MAIN"})  # B share
    assert not snapshot_row_is_target({"symbol": "200413.SZ", "board": "SZSE_MAIN"})  # B share
    assert not snapshot_row_is_target({"symbol": "200771.SZ", "board": "SZSE_MAIN"})  # B share
    assert not snapshot_row_is_target({"symbol": "689009.SH", "board": "STAR"})  # CDR
    assert not snapshot_row_is_target({"symbol": "302132.SZ", "board": "SZSE_MAIN"})  # invalid target code range

    # An explicit non-target type must fail closed even if the code itself looks like an A share.
    assert not snapshot_row_is_target({
        "symbol": "600000.SH",
        "board": "SSE_MAIN",
        "security_type": "B_SHARE",
    })
