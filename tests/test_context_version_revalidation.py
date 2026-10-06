"""C06: New Context version re-run 2A/2B; old evidence cannot authorize new context."""
from __future__ import annotations

import hashlib
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from a_share_agent.backtest.oos_stability import (
    build_per_key_oos_artifact,
    per_key_oos_stability,
    verify_per_key_oos_artifact,
)
from a_share_agent.strategy.enablement import (
    PolicyEntry,
    RULE_HASH_SCHEMA,
    verify_physical_artifact,
)
from a_share_agent.market.context import MarketContextBuilder


# ── Constants ──

KEY = ("BULL", "ACTIVE", "P1", "v1")

_OLD_CONTEXT_HASH = hashlib.sha256(b'{"version":"0.7.0"}').hexdigest()
_NEW_CONTEXT_HASH = hashlib.sha256(b'{"version":"0.8.0"}').hexdigest()

STABLE_HASHES = {f"{name}_sha256": hashlib.sha256(name.encode()).hexdigest()
                 for name in ("router", "scanner", "regime", "context", "scoring", "cost_source")}

OLD_RULE_HASHES = dict(STABLE_HASHES, context_sha256=_OLD_CONTEXT_HASH)
NEW_RULE_HASHES = dict(STABLE_HASHES, context_sha256=_NEW_CONTEXT_HASH)


# ── Helpers ──

def _make_reports(n_folds: int = 4) -> list[dict]:
    reports = []
    for i in range(n_folds):
        trades = []
        for j in range(10):
            common = dict(
                round_trip_id=f"{i}-{j}",
                regime_at_signal=KEY[0],
                theme_lifecycle=KEY[1],
                pattern_id=KEY[2],
                pattern_version=KEY[3],
                regime_data_quality_at_signal={"state": "ok"},
                theme_data_quality_at_signal={"state": "ok"},
                trade_date="2024-01-01",
                exit_reason="SIGNAL:test",
            )
            trades.append(dict(common, direction="BUY", pnl_pct=None))
            trades.append(
                dict(common, direction="SELL", pnl_pct=0.02 if j < 9 else -0.01)
            )
        reports.append(
            dict(
                fold_id=i,
                complete=True,
                status="COMPLETED",
                coverage=1.0,
                trades=trades,
            )
        )
    return reports


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _dump(p: Path, value) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, sort_keys=True, allow_nan=False))


def _build_published_directory(
    tmp_path: Path,
    rule_hashes: dict,
    clock: float,
) -> tuple[Path, dict, dict]:
    """Create a fully publishable evidence directory."""
    directory = tmp_path / "published"
    directory.mkdir()
    inp = tmp_path / "fixture.csv"
    inp.write_text("synthetic-only\n")

    reports = _make_reports()
    for i, r in enumerate(reports):
        _dump(directory / f"fold_{i}" / "report.json", r)

    from a_share_agent.backtest.data import ConsumedInputLedger

    ledger = ConsumedInputLedger(tmp_path)
    for i in range(4):
        ledger.fold_id = str(i)
        ledger.read(inp, kind="synthetic_fixture")
        events = [e for e in ledger.events if e["fold_id"] == str(i)]
        _dump(
            directory / f"folds/{i}/consumed_inputs.json",
            dict(events=events, sha256=ledger.digest(events), status="VERIFIED"),
        )
    snapshot = ledger.snapshot(verify_files=True)

    now_ts = clock - 100
    generated_at = datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat()

    manifest = dict(
        run_id="cv-test",
        source_sha="test-source-sha",
        generation_id="cv-test-generation",
        overall_status="COMPLETED",
        completion=True,
        artifact_path="oos_per_key.json",
        config_hash="ch",
        wf_config_hash="wch",
        rule_hashes=dict(rule_hashes),
        dirty=False,
        generated_at=generated_at,
        consumed_universes={
            "0": {
                "symbols": ["FIXTURE"],
                "universe_sha256": hashlib.sha256(b'["FIXTURE"]').hexdigest(),
            }
        },
        physical_provenance={"status": "VERIFIED"},
        physical_inputs=snapshot["physical_inputs"],
        fold_plan={"folds": [{"fold_id": i} for i in range(4)]},
        settings={"stability_thresholds": {}},
        declared_keys=[list(KEY)],
        project_root=str(tmp_path),
        consumed_input_ledger=snapshot,
        consumed_input_ledger_sha256=snapshot["sha256"],
    )

    result = per_key_oos_stability(
        reports, expected_fold_ids=list(range(4)), declared_keys=[KEY]
    )
    assert result["keys"]["::".join(KEY)]["classification"] == "STABLE_CANDIDATE"

    artifact = build_per_key_oos_artifact(
        result,
        "cv-test",
        "test-source-sha",
        "ch",
        "wch",
        dict(rule_hashes),
        manifest,
    )
    artifact["generated_at"] = generated_at

    _publish(directory, artifact, manifest)
    return directory, artifact, manifest


def _publish(directory: Path, artifact: dict, manifest: dict) -> None:
    """Write artifact, manifest, completion marker."""
    artifact.pop("content_hash", None)
    canonical = json.dumps(
        artifact, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    artifact["content_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
    _dump(directory / "oos_per_key.json", artifact)
    manifest["artifact_sha256"] = _digest(directory / "oos_per_key.json")
    manifest["oos_per_key_sha256"] = artifact["content_hash"]
    manifest["output_files"] = {
        str(p.relative_to(directory)): _digest(p)
        for p in directory.rglob("*.json")
        if p.name not in ("manifest.json", "completion.json")
    }
    _dump(directory / "manifest.json", manifest)
    _dump(
        directory / "completion.json",
        dict(
            run_id=manifest["run_id"],
            generation_id=manifest["generation_id"],
            manifest_sha256=_digest(directory / "manifest.json"),
        ),
    )


# ── Fixtures ──

@pytest.fixture
def clock(monkeypatch):
    now = [2000000000.0]
    monkeypatch.setattr("time.time", lambda: now[0])
    return now


@pytest.fixture
def published_new(tmp_path, clock):
    """Published evidence with NEW (0.8.0) context version hashes."""
    return _build_published_directory(tmp_path, NEW_RULE_HASHES, clock[0])


@pytest.fixture
def published_old(tmp_path, clock):
    """Published evidence with OLD (0.7.0) context version hashes."""
    return _build_published_directory(tmp_path, OLD_RULE_HASHES, clock[0])


# ── Test 1: context_version tracking ──

class TestContextVersionTracking:
    """1. context_version is tracked in ContextSnapshot, PolicyEntry, OOS artifact."""

    def test_context_version_in_market_context_build(self):
        """MarketContextBuilder.build() output contains context_version."""
        source = inspect.getsource(MarketContextBuilder.build)
        assert '"context_version"' in source, (
            "MarketContextBuilder.build must reference context_version"
        )
        assert '"0.8.0"' in source, (
            "MarketContextBuilder.build context_version must be '0.8.0'"
        )

    def test_context_version_in_policy_entry(self, published_new, clock):
        """PolicyEntry carries context_version from verified PhysicalEvidence."""
        directory, _, _ = published_new
        evidence = verify_physical_artifact(directory, KEY)
        entry = PolicyEntry.from_evidence(KEY, evidence)
        assert hasattr(entry, "context_version")
        assert isinstance(entry.context_version, str)
        assert len(entry.context_version) == 64
        assert entry.context_version == evidence.context_version

    def test_context_version_in_oos_artifact(self):
        """OOS artifact rule_hashes includes context_sha256."""
        reports = _make_reports()
        result = per_key_oos_stability(
            reports, expected_fold_ids=list(range(4)), declared_keys=[KEY]
        )
        artifact = build_per_key_oos_artifact(
            result,
            run_id="cv-test",
            source_sha="sha",
            config_hash="ch",
            wf_config_hash="wch",
            rule_hashes=dict(NEW_RULE_HASHES),
        )
        assert "rule_hashes" in artifact
        assert "context_sha256" in artifact["rule_hashes"]
        assert artifact["rule_hashes"]["context_sha256"] == _NEW_CONTEXT_HASH

    def test_context_version_in_physical_evidence(self, published_new):
        """PhysicalEvidence carries context_version from verification."""
        directory, _, _ = published_new
        evidence = verify_physical_artifact(directory, KEY)
        assert hasattr(evidence, "context_version")
        assert evidence.context_version == _NEW_CONTEXT_HASH


# ── Test 2: Old context version CANNOT authorize new context ──

class TestOldContextRejected:
    """2. Old context version evidence CANNOT authorize a new context version."""

    def test_old_evidence_fails_with_new_config(self, published_old):
        """Mismatched rule_hashes between artifact and config raises."""
        directory, _, _ = published_old

        class NewConfig:
            rule_hashes = NEW_RULE_HASHES
            config_hash = "ch"

        with pytest.raises(ValueError, match="rule hash"):
            verify_physical_artifact(directory, KEY, config=NewConfig())

    def test_old_evidence_policy_loads_with_old_version(self, published_old, clock):
        """Policy from old-context evidence carries old context_version."""
        directory, _, _ = published_old
        evidence = verify_physical_artifact(directory, KEY)
        assert evidence.context_version == _OLD_CONTEXT_HASH

        entry = PolicyEntry.from_evidence(KEY, evidence).approve("operator")
        assert entry.context_version == _OLD_CONTEXT_HASH
        assert entry.is_enabled

    def test_forged_context_version_in_snapshot_rejected(self, published_old, clock):
        """A snapshot with a tampered context_version is rejected by from_dict."""
        directory, _, _ = published_old
        evidence = verify_physical_artifact(directory, KEY)
        entry = PolicyEntry.from_evidence(KEY, evidence).approve("operator")
        snapshot = entry.to_dict()
        snapshot["context_version"] = _NEW_CONTEXT_HASH
        with pytest.raises(ValueError, match="binding|mismatch"):
            PolicyEntry.from_dict(snapshot)


# ── Test 3: Matching context versions authorize correctly ──

class TestMatchingContextVersion:
    """3. Matching context versions authorize correctly."""

    def test_matching_version_verifies(self, published_new):
        """verify_physical_artifact passes with matching context hash."""
        directory, _, _ = published_new
        evidence = verify_physical_artifact(directory, KEY)
        assert evidence.context_version == _NEW_CONTEXT_HASH
        assert evidence.verification_result == "VERIFIED"

    def test_matching_version_policy_enabled(self, published_new, clock):
        """PolicyEntry from matching evidence is enabled."""
        directory, _, _ = published_new
        evidence = verify_physical_artifact(directory, KEY)
        entry = PolicyEntry.from_evidence(KEY, evidence).approve("operator")
        assert entry.context_version == _NEW_CONTEXT_HASH
        assert entry.is_enabled
        assert entry.is_valid

    def test_matching_version_snapshot_roundtrip(self, published_new, clock, tmp_path):
        """Policy survives snapshot roundtrip when context matches."""
        directory, _, _ = published_new
        evidence = verify_physical_artifact(directory, KEY)
        entry = PolicyEntry.from_evidence(KEY, evidence).approve("operator")

        snap_path = tmp_path / "policy.json"
        entry.to_snapshot(snap_path)
        reloaded = PolicyEntry.from_snapshot(snap_path)
        assert reloaded == entry
        assert reloaded.context_version == _NEW_CONTEXT_HASH
        assert reloaded.is_enabled


# ── Test 4: Walk-forward artifact binding ──

class TestWalkForwardArtifactBinding:
    """4. Walk-forward artifact embeds context version with producer SHA."""

    def test_oos_artifact_embeds_context_version_and_source_sha(self):
        """build_per_key_oos_artifact includes rule_hashes and source_sha."""
        reports = _make_reports()
        result = per_key_oos_stability(
            reports, expected_fold_ids=list(range(4)), declared_keys=[KEY]
        )
        source_sha = "abc123deadbeef" * 4
        artifact = build_per_key_oos_artifact(
            result,
            run_id="wf-binding",
            source_sha=source_sha,
            config_hash="ch",
            wf_config_hash="wch",
            rule_hashes=dict(NEW_RULE_HASHES),
        )
        assert artifact["source_sha"] == source_sha
        assert artifact["rule_hashes"]["context_sha256"] == _NEW_CONTEXT_HASH
        assert isinstance(artifact["content_hash"], str)
        assert len(artifact["content_hash"]) == 64

    def test_content_hash_binds_context_and_source(self):
        """content_hash changes when context_version or source_sha changes."""
        reports = _make_reports()
        result = per_key_oos_stability(
            reports, expected_fold_ids=list(range(4)), declared_keys=[KEY]
        )

        def make(source: str, hashes: dict) -> str:
            a = build_per_key_oos_artifact(
                result,
                run_id="hash-binding",
                source_sha=source,
                config_hash="ch",
                wf_config_hash="wch",
                rule_hashes=hashes,
            )
            return a["content_hash"]

        base = make("sha-a" * 8, NEW_RULE_HASHES)
        assert make("sha-b" * 8, NEW_RULE_HASHES) != base
        assert make("sha-a" * 8, OLD_RULE_HASHES) != base

    def test_oos_artifact_verify_binds_context_and_source(self, published_new):
        """verify_per_key_oos_artifact validates context hash + SHA binding."""
        directory, artifact, _ = published_new

        v = verify_per_key_oos_artifact(
            artifact,
            expected_source_sha="test-source-sha",
            manifest_path=str(directory / "manifest.json"),
        )
        assert v["valid"]

        v2 = verify_per_key_oos_artifact(
            artifact,
            expected_source_sha="wrong-sha",
            manifest_path=str(directory / "manifest.json"),
        )
        assert not v2["valid"]

        tampered = dict(artifact)
        tampered["rule_hashes"] = dict(
            artifact["rule_hashes"], context_sha256=_OLD_CONTEXT_HASH
        )
        v3 = verify_per_key_oos_artifact(
            tampered, manifest_path=str(directory / "manifest.json")
        )
        assert not v3["valid"]
