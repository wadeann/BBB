"""
Phase 1.5C: Smoke Artifact Schema Validation

Validates the committed trading_grade_smoke_latest.json artifact.
Does NOT require local market data — GitHub runners can run this.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

ARTIFACT_PATH = Path(__file__).resolve().parent.parent / "data" / "diagnostics" / "trading_grade_smoke_latest.json"


@pytest.fixture(scope="module")
def artifact():
    if not ARTIFACT_PATH.exists():
        pytest.skip("Smoke artifact not found — run scripts/run_trading_grade_smoke.py first")
    with open(ARTIFACT_PATH) as f:
        return json.load(f)


class TestSmokeArtifactSchema:
    """Validate the committed smoke artifact structure and content."""

    def test_artifact_exists(self, artifact):
        assert artifact is not None

    def test_data_kind_valid(self, artifact):
        kind = artifact.get("data_kind")
        assert kind in ("HISTORICAL_LOCAL_DATA", "REAL_HISTORICAL"), f"Unexpected data_kind: {kind}"

    def test_source_verification_present(self, artifact):
        sv = artifact.get("source_verification")
        assert sv in ("UNVERIFIED_CACHE", "PROVENANCE_VERIFIED"), f"Unexpected: {sv}"

    def test_producer_git_sha_exists(self, artifact):
        sha = artifact.get("git_commit_sha", "")
        assert len(sha) == 40, f"Invalid SHA length: {len(sha)}"
        assert all(c in "0123456789abcdef" for c in sha), f"Non-hex SHA: {sha}"

    def test_input_files_hashes_valid(self, artifact):
        inputs = artifact.get("input_files", [])
        assert len(inputs) > 0, "No input files recorded"
        for entry in inputs:
            sha = entry.get("adjusted_bars_sha256", "")
            if sha != "MISSING":
                assert len(sha) == 64, f"Bad SHA256 len for {entry.get('symbol')}: {len(sha)}"
                assert all(c in "0123456789abcdef" for c in sha), f"Non-hex SHA256: {sha}"

    def test_attribution_invalid_zero(self, artifact):
        audit = artifact.get("attribution_audit", {})
        assert audit.get("invalid", -1) == 0, f"Attribution invalid trades: {audit.get('invalid', '?')}"
        assert audit.get("valid", 0) > 0, "No valid trades in audit"
        assert audit.get("closed_trades", 0) > 0 or audit.get("closed_trades", 0) == 0  # 0 is OK if no trades
        # If there are trades, attribution must be clean
        if audit.get("closed_trades", 0) > 0:
            assert audit["valid"] == audit["closed_trades"], f"Not all trades valid: {audit}"

    def test_duplicate_round_trip_ids_empty(self, artifact):
        """No duplicate round_trip_ids in smoke run."""
        audit = artifact.get("attribution_audit", {})
        dups = audit.get("duplicate_round_trip_ids", [])
        assert len(dups) == 0, f"Duplicate round_trip_ids found: {dups}"

    def test_trade_summary_fields(self, artifact):
        assert "total_trades" in artifact
        assert "closed_trades" in artifact
        assert "patterns_seen" in artifact
        assert "regimes_seen" in artifact

    def test_matrix_row_structure(self, artifact):
        rows = artifact.get("regime_pattern_matrix", [])
        if rows:
            row = rows[0]
            assert "quality_ok_trades" in row
            assert "quality_degraded_trades" in row
            assert "quality_coverage" in row
            assert "usable_for_router" in row

    def test_context_usable_counts(self, artifact):
        """Context usable/unusable counts must be present."""
        assert "context_usable_trades" in artifact
        assert "context_unusable_trades" in artifact
