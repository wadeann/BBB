import json
from pathlib import Path
from types import SimpleNamespace

from a_share_agent.backtest.research import _official_universe_audit, _official_universe_reconciliation
from a_share_agent.backtest.universe_reconciliation import reconcile_universe_snapshot_counts


def _write_fixture(root: Path) -> None:
    backtest = root / "data" / "backtest"
    snapshots = backtest / "official_universe_snapshots"
    snapshots.mkdir(parents=True)
    (backtest / "security_master.csv").write_text(
        "symbol,name,active_from,active_to,board,listing_date,delisting_date\n"
        "600000.SH,target,1999-11-10,,SSE_MAIN,1999-11-10,\n"
        "900957.SH,b-share,2000-07-28,,SSE_MAIN,2000-07-28,\n"
        "689009.SH,cdr,2020-10-29,,STAR,2020-10-29,\n"
        "600001.SH,future,2027-01-01,,SSE_MAIN,2027-01-01,\n",
        encoding="utf-8",
    )
    (snapshots / "2026-09-30.csv").write_text(
        "date,symbol,exchange,board,security_type,listing_date\n"
        "2026-09-30,600000.SH,SSE,SSE_MAIN,A_SHARE_COMMON_EQUITY,1999-11-10\n"
        "2026-09-30,600002.SH,SSE,SSE_MAIN,A_SHARE_COMMON_EQUITY,2001-01-01\n"
        # Even if stale source data mislabels these as common equity, the code/board
        # classifier must keep them out of the target A-share set.
        "2026-09-30,900957.SH,SSE,SSE_MAIN,A_SHARE_COMMON_EQUITY,2000-07-28\n"
        "2026-09-30,689009.SH,SSE,STAR,A_SHARE_COMMON_EQUITY,2020-10-29\n",
        encoding="utf-8",
    )


def test_preflight_universe_counts_share_provenance_semantics(tmp_path: Path):
    _write_fixture(tmp_path)
    expected = reconcile_universe_snapshot_counts(
        tmp_path / "data" / "backtest" / "security_master.csv",
        tmp_path / "data" / "backtest" / "official_universe_snapshots",
    )
    assert expected["snapshot_count"] == 1
    assert expected["missing_total"] == 1
    assert expected["extra_total"] == 0
    assert expected["per_snapshot"][0]["missing_symbols"] == ["600002.SH"]

    config = SimpleNamespace(project_root=tmp_path)
    via_research = _official_universe_reconciliation(config)
    assert via_research == expected

    match, extra, missing = _official_universe_audit(config, mcp=None, overrides=None)
    assert match is False
    assert extra == expected["extra_total"]
    assert missing == expected["missing_total"]


def test_committed_provenance_artifact_matches_preflight_reconciliation():
    root = Path(__file__).resolve().parents[1]
    artifact = json.loads((root / "historical_data_provenance_audit.json").read_text(encoding="utf-8"))
    expected = artifact["universe"]
    actual = reconcile_universe_snapshot_counts(
        root / "data" / "backtest" / "security_master.csv",
        root / "data" / "backtest" / "official_universe_snapshots",
    )
    assert actual["snapshot_count"] == expected["snapshot_count"]
    assert actual["missing_total"] == expected["missing_total"]
    assert actual["extra_total"] == expected["extra_total"]
    assert actual["match"] == expected["match"]
