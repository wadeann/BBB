from scripts.audit_historical_data_provenance import _snapshot_row_is_target


def test_legacy_snapshot_asset_filter_matches_preflight_rules():
    assert _snapshot_row_is_target({"symbol": "600000.SH", "board": "SSE_MAIN"})
    assert _snapshot_row_is_target({"symbol": "688981.SH", "board": "STAR"})
    assert _snapshot_row_is_target({"symbol": "000001.SZ", "board": "SZSE_MAIN"})
    assert _snapshot_row_is_target({"symbol": "300750.SZ", "board": "CHINEXT"})
    assert _snapshot_row_is_target({"symbol": "920002.BJ", "board": "BSE"})

    # Legacy snapshots may not have a security_type column. These must still be excluded.
    assert not _snapshot_row_is_target({"symbol": "900957.SH", "board": "SSE_MAIN"})  # B share
    assert not _snapshot_row_is_target({"symbol": "200413.SZ", "board": "SZSE_MAIN"})  # B share
    assert not _snapshot_row_is_target({"symbol": "200771.SZ", "board": "SZSE_MAIN"})  # B share
    assert not _snapshot_row_is_target({"symbol": "689009.SH", "board": "STAR"})  # CDR
    assert not _snapshot_row_is_target({"symbol": "302132.SZ", "board": "SZSE_MAIN"})  # invalid target code range

    # An explicit non-target type must fail closed even if the code itself looks like an A share.
    assert not _snapshot_row_is_target({
        "symbol": "600000.SH",
        "board": "SSE_MAIN",
        "security_type": "B_SHARE",
    })
