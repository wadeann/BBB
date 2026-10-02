import json
from pathlib import Path

import pytest

from a_share_agent.backtest.universe_source_semantics import (
    require_delisting_field_semantics,
    validate_universe_source_semantics,
)


def _write_semantics(root: Path, *, allow_sse: bool) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "source_semantics.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": {
                    "szse_delisted_register.csv": {
                        "raw_date_field": "终止上市日期",
                        "normalized_field": "delisting_date",
                        "semantic_status": "EXPLICIT_TERMINATION_FIELD",
                        "allow_as_delisting_date": True,
                    },
                    "sse_delisted_register.csv": {
                        "raw_date_field": "暂停上市日期",
                        "normalized_field": "delisting_date",
                        "semantic_status": (
                            "SOURCE_WIDE_VERIFIED_TERMINATION_EFFECTIVE_DATE"
                            if allow_sse
                            else "PROVISIONAL_SAMPLE_VERIFIED_NOT_SOURCE_WIDE"
                        ),
                        "allow_as_delisting_date": allow_sse,
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "sse_delisting_date_verification.csv").write_text(
        "symbol,raw_field,raw_date,verified_delisting_date,verification_status,official_announcement_url,evidence_note\n"
        "600608.SH,暂停上市日期,2026-07-03,2026-07-03,MATCH,https://www.sse.com.cn/example,verified sample\n",
        encoding="utf-8",
    )


def test_sample_matches_do_not_promote_ambiguous_source(tmp_path: Path):
    raw = tmp_path / "raw_registers"
    _write_semantics(raw, allow_sse=False)
    audit = validate_universe_source_semantics(raw)
    assert audit["sse_sample_evidence_valid"] is True
    assert audit["sse_verification_sample_count"] == 1
    assert audit["source_wide_verified"] is False
    assert audit["ready"] is False
    assert audit["reason"] == "SSE_DELISTING_DATE_FIELD_SEMANTICS_NOT_SOURCE_WIDE_VERIFIED"
    with pytest.raises(RuntimeError, match="unverified delisting-date semantics"):
        require_delisting_field_semantics(raw, "sse_delisted_register.csv")


def test_source_wide_semantics_contract_allows_mapping(tmp_path: Path):
    raw = tmp_path / "raw_registers"
    _write_semantics(raw, allow_sse=True)
    audit = validate_universe_source_semantics(raw)
    assert audit["source_wide_verified"] is True
    assert audit["ready"] is True
    entry = require_delisting_field_semantics(raw, "sse_delisted_register.csv")
    assert entry["raw_date_field"] == "暂停上市日期"


def test_repo_semantics_remain_fail_closed_until_sse_source_wide_verification():
    root = Path(__file__).resolve().parents[1]
    raw = root / "data" / "backtest" / "official_universe_snapshots" / "raw_registers"
    audit = validate_universe_source_semantics(raw)
    assert audit["manifest_present"] is True
    assert audit["sse_sample_evidence_valid"] is True
    assert audit["sse_verification_sample_count"] >= 4
    assert audit["sources"]["szse_delisted_register.csv"]["allow_as_delisting_date"] is True
    assert audit["sources"]["sse_delisted_register.csv"]["allow_as_delisting_date"] is False
    assert audit["source_wide_verified"] is False
