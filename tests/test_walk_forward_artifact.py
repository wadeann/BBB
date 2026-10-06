"""Walk-forward stability artifact tests.

Tests for run_stability helpers, persistence, and artifact integrity.
Run with: pytest -xvs tests/test_walk_forward_artifact.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from a_share_agent.backtest.walk_forward_service import run_stability


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def temp_output() -> Path:
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


# ====== AS1: run_stability is callable ======
class TestRunStabilitySignature:
    """Verify run_stability function exists and is callable."""

    def test_run_stability_exists(self):
        assert callable(run_stability)


# ====== AS2: _safe_run_id ======
class TestSafeRunId:
    """_safe_run_id generates safe IDs from various inputs."""

    def test_safe_run_id_from_valid(self):
        from a_share_agent.backtest.walk_forward_service import _safe_run_id
        rid = _safe_run_id("my-run-001")
        assert rid == "my-run-001"

    def test_safe_run_id_sanitizes(self):
        from a_share_agent.backtest.walk_forward_service import _safe_run_id
        rid = _safe_run_id("bad chars!@#$")
        assert all(c.isalnum() or c in "-_" for c in rid)
        assert rid.startswith("wf-") or rid != "bad chars!@#$"

    def test_safe_run_id_none(self):
        from a_share_agent.backtest.walk_forward_service import _safe_run_id
        rid = _safe_run_id(None)
        assert rid.startswith("wf-") and len(rid) > 3


# ====== AS3: _is_incomplete_run ======
class TestIsIncompleteRun:
    """_is_incomplete_run detects runs without manifest."""

    def test_empty_dir_is_incomplete(self):
        from a_share_agent.backtest.walk_forward_service import _is_incomplete_run
        with tempfile.TemporaryDirectory() as td:
            assert _is_incomplete_run(Path(td))

    def test_dir_with_manifest_only_is_incomplete(self):
        from a_share_agent.backtest.walk_forward_service import _is_incomplete_run
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "manifest.json"
            p.write_text("{}", encoding="utf-8")
            assert _is_incomplete_run(Path(td))


# ====== AS4: _has_matching_run_id ======
class TestHasMatchingRunId:
    """_has_matching_run_id checks manifest run_id."""

    def test_matching_id(self):
        from a_share_agent.backtest.walk_forward_service import _has_matching_run_id
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "manifest.json"
            manifest.write_text(json.dumps({"run_id": "wf-test-001"}), encoding="utf-8")
            assert _has_matching_run_id(Path(td), "wf-test-001")

    def test_non_matching_id(self):
        from a_share_agent.backtest.walk_forward_service import _has_matching_run_id
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "manifest.json"
            manifest.write_text(json.dumps({"run_id": "wf-test-001"}), encoding="utf-8")
            assert not _has_matching_run_id(Path(td), "other-id")

    def test_missing_manifest(self):
        from a_share_agent.backtest.walk_forward_service import _has_matching_run_id
        with tempfile.TemporaryDirectory() as td:
            assert not _has_matching_run_id(Path(td), "any-id")


# ====== AS5: _list_non_empty ======
class TestListNonEmpty:
    """_list_non_empty detects non-empty output directories."""

    def test_empty_dir_returns_empty(self):
        from a_share_agent.backtest.walk_forward_service import _list_non_empty
        with tempfile.TemporaryDirectory() as td:
            assert _list_non_empty(Path(td)) == []

    def test_non_empty_dir_returns_files(self):
        from a_share_agent.backtest.walk_forward_service import _list_non_empty
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "report.json").write_text("{}")
            assert len(_list_non_empty(Path(td))) == 1


# ====== AS6: _assert_finite_json ======
class TestAssertFiniteJSON:
    """_assert_finite_json rejects NaN/Infinity."""

    def test_finite_json_ok(self):
        from a_share_agent.backtest.walk_forward_service import _assert_finite_json
        _assert_finite_json('{"a": 1.5, "b": null}')

    def test_nan_raises(self):
        from a_share_agent.backtest.walk_forward_service import _assert_finite_json
        with pytest.raises(ValueError, match="Non-finite"):
            _assert_finite_json('{"a": NaN}')

    def test_infinity_raises(self):
        from a_share_agent.backtest.walk_forward_service import _assert_finite_json
        with pytest.raises(ValueError, match="Non-finite"):
            _assert_finite_json('{"a": Infinity}')


# ====== AS7: _write_finite_json ======
class TestWriteFiniteJSON:
    """write_finite_json writes valid JSON files."""

    def test_write_finite_json_roundtrip(self):
        from a_share_agent.backtest.walk_forward_service import _write_finite_json
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.json"
            _write_finite_json(p, {"a": 1, "b": "hello"})
            assert p.exists()
            data = json.loads(p.read_text(encoding="utf-8"))
            assert data["a"] == 1
            assert data["b"] == "hello"


# ====== AS8: write_oos_per_key persistence ======
class TestWriteOOSPerKey:
    """write_oos_per_key writes artifact and returns content_hash."""

    def test_write_oos_per_key_creates_file(self):
        from a_share_agent.backtest.walk_forward_persistence import write_oos_per_key
        from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact, per_key_oos_stability

        result = per_key_oos_stability([])
        artifact = build_per_key_oos_artifact(result, run_id="test", source_sha="s",
                                              config_hash="c", wf_config_hash="w",
                                              rule_hashes={})
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "oos_per_key.json"
            ch = write_oos_per_key(p, artifact)
            assert p.exists()
            assert ch == artifact.get("content_hash", "")
            loaded = json.loads(p.read_text(encoding="utf-8"))
            assert loaded["run_id"] == "test"

    def test_write_oos_per_key_finite_json(self):
        from a_share_agent.backtest.walk_forward_persistence import write_oos_per_key
        from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact, per_key_oos_stability

        result = per_key_oos_stability([])
        artifact = build_per_key_oos_artifact(result, run_id="t", source_sha="s",
                                              config_hash="c", wf_config_hash="w",
                                              rule_hashes={})
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "oos_per_key.json"
            write_oos_per_key(p, artifact)
            raw = p.read_text(encoding="utf-8")
            assert "NaN" not in raw
            assert "Infinity" not in raw

def test_run_stability_records_fold_exception(monkeypatch, temp_output):
    """A failed fold is recorded and does not raise NameError on fold_errors."""
    from types import SimpleNamespace
    from a_share_agent.backtest import walk_forward_service as service

    monkeypatch.setattr(service, "_WF_AVAILABLE", True)
    monkeypatch.setattr(service, "_PREFLIGHT_AVAILABLE", True)
    monkeypatch.setattr(service, "_OOS_AVAILABLE", True)
    monkeypatch.setattr(service, "WalkForwardConfig", lambda **kwargs: SimpleNamespace(
        stability_thresholds={}, universe=(), warmup_bars=kwargs.get("warmup_bars", 260), canonical_hash=lambda: "wfhash",
        to_dict=lambda: {"run_id": "exception-test"}))
    fold = {"fold_id": "f1", "train_start": "2020-01-01", "train_end_exclusive": "2021-01-01",
            "test_start": "2021-01-01", "test_end_exclusive": "2021-04-01", "complete": True}
    monkeypatch.setattr(service, "generate_folds", lambda cfg: ([dict(fold)], None))
    monkeypatch.setattr(service, "HistoricalDataProvider", lambda *a, **k: SimpleNamespace(
        load_universe_for_period=lambda *a, **k: SimpleNamespace(symbols=["X"])))
    monkeypatch.setattr(service, "settings_from", lambda *a, **k: SimpleNamespace(
        universe_file="u.csv", max_universe=10, universe_mode="configured"))
    monkeypatch.setattr(service, "preflight_fold", lambda *a, **k: ("READY", True, [], {}))
    monkeypatch.setattr(service, "BacktestEngine", lambda *a, **k: SimpleNamespace(
        run=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("forced fold failure"))))
    monkeypatch.setattr(service, "get_git_metadata", lambda *a, **k: {"git_commit_sha": "source"})
    monkeypatch.setattr(service, "_compute_rule_hashes", lambda *a, **k: {})
    per_key_calls = []
    def record_per_key_call(*args, **kwargs):
        per_key_calls.append(kwargs.get("expected_fold_ids"))
        return {"keys": {}, "n_keys": 0, "classification_summary": {},
                "attribution_valid": False, "plan_complete": False}
    monkeypatch.setattr(service, "per_key_oos_stability", record_per_key_call)
    monkeypatch.setattr(service, "build_per_key_oos_artifact", lambda *a, **k: {})
    result = service.run_stability(
        SimpleNamespace(project_root=temp_output, defaults={"benchmarks": {}}, config_hash="c"),
        {"start_date": "2020-01-01", "end_date": "2021-04-01", "universe": ["X"], "settings": {}},
        temp_output / "run", run_id="exception-test")
    assert result["failed"] == 1
    methodology = json.loads((temp_output / "run" / "methodology.json").read_text(encoding="utf-8"))
    assert methodology["fold_errors"][0]["status"] == "RUN_FAILED"
    diagnostics = json.loads((temp_output / "run" / "diagnostics.json").read_text(encoding="utf-8"))
    assert per_key_calls == [["f1"]]
    assert "forced fold failure" in diagnostics["fold_errors"][0]["error"]


# ====== AS9: write_oos_stability persistence ======
class TestWriteOOSStability:
    """write_oos_stability writes JSON and CSV output files."""

    def test_write_oos_stability_creates_files(self):
        from a_share_agent.backtest.walk_forward_persistence import write_oos_stability
        with tempfile.TemporaryDirectory() as td:
            json_path = Path(td) / "oos_stability.json"
            csv_path = Path(td) / "oos_stability.csv"
            stability = {"classification": "STABLE_CANDIDATE", "n_total_closed": 40,
                         "n_observed_folds": 4, "n_informative_folds": 3, "n_failed_folds": 0}
            summaries = [{"fold_id": 1, "n_closed": 10, "expectancy_pct": 0.02}]
            write_oos_stability(json_path, csv_path, stability, summaries)
            assert json_path.exists()
            assert csv_path.exists()


# ====== AS10: write_fold_artifacts persistence ======
class TestWriteFoldArtifacts:
    """write_fold_artifacts writes report, trades CSV, and matrix CSV."""

    def test_write_fold_artifacts_creates_files(self):
        from a_share_agent.backtest.walk_forward_persistence import write_fold_artifacts
        with tempfile.TemporaryDirectory() as td:
            fold_dir = Path(td) / "fold_001"
            report = {
                "fold_id": "fold_001",
                "trades": [{"symbol": "000001.SZ", "direction": "BUY", "pnl_pct": 0.05}],
                "regime_pattern_matrix": [{"regime": "BULL", "count": 5}],
            }
            write_fold_artifacts(fold_dir, report)
            assert (fold_dir / "report.json").exists()
            assert (fold_dir / "trades.csv").exists()
            assert (fold_dir / "matrix.csv").exists()


# ====== AS11: Key encoding helpers are importable ======
class TestKeyEncodingImports:
    """_key_to_artifact_key / _artifact_key_to_tuple are usable."""

    def test_roundtrip_simple(self):
        from a_share_agent.backtest.oos_stability import _key_to_artifact_key, _artifact_key_to_tuple
        key = ("BULL", "ACTIVE", "MORNING_STAR", "v1")
        encoded = _key_to_artifact_key(key)
        decoded = _artifact_key_to_tuple(encoded)
        assert decoded == key

    def test_roundtrip_with_delimiter_chars(self):
        from a_share_agent.backtest.oos_stability import _key_to_artifact_key, _artifact_key_to_tuple
        # Keys containing '::' or '%' must survive roundtrip
        key_a = ("BEAR", "DECLINING", "P::v1", "v2")
        key_b = ("BULL", "ACTIVE", "P", "v1::v2")
        enc_a = _key_to_artifact_key(key_a)
        enc_b = _key_to_artifact_key(key_b)
        assert enc_a != enc_b, "Collision: encoded keys should differ"
        assert _artifact_key_to_tuple(enc_a) == key_a
        assert _artifact_key_to_tuple(enc_b) == key_b


# ====== AS12: verify_per_key_oos_artifact importable and functional ======
class TestVerifyPerKeyArtifact:
    """verify_per_key_oos_artifact checks integrity and eligibility."""

    def test_verify_unbound_artifact_is_invalid(self):
        from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact, verify_per_key_oos_artifact, per_key_oos_stability

        result = per_key_oos_stability([])
        artifact = build_per_key_oos_artifact(result, run_id="test", source_sha="s",
                                              config_hash="c", wf_config_hash="w",
                                              rule_hashes={})
        v = verify_per_key_oos_artifact(artifact)
        assert v["valid"] is False

    def test_verify_corrupt_hash(self):
        from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact, verify_per_key_oos_artifact, per_key_oos_stability

        result = per_key_oos_stability([])
        artifact = build_per_key_oos_artifact(result, run_id="test", source_sha="s",
                                              config_hash="c", wf_config_hash="w",
                                              rule_hashes={})
        artifact["content_hash"] = "tampered"
        v = verify_per_key_oos_artifact(artifact)
        assert v["valid"] is False
        assert any("hash" in r for r in v["reasons"])

    def test_verify_tampered_key_eligible_false(self):
        from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact, verify_per_key_oos_artifact, per_key_oos_stability

        result = per_key_oos_stability([])
        artifact = build_per_key_oos_artifact(result, run_id="test", source_sha="s",
                                              config_hash="c", wf_config_hash="w",
                                              rule_hashes={})
        artifact["content_hash"] = "tampered"
        v = verify_per_key_oos_artifact(artifact, request_key=("BULL", "ACTIVE", "P1", "v1"))
        # key_eligible must be False when artifact is invalid
        assert v["key_eligible"] is False

    def test_verify_missing_source_sha(self):
        from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact, verify_per_key_oos_artifact, per_key_oos_stability

        result = per_key_oos_stability([])
        artifact = build_per_key_oos_artifact(result, run_id="test", source_sha="",
                                              config_hash="c", wf_config_hash="w",
                                              rule_hashes={})
        v = verify_per_key_oos_artifact(artifact)
        assert v["valid"] is False
        assert any("source_sha" in r for r in v["reasons"])


# ====== AS13: per_key_oos_stability handles empty fold list ======
class TestPerKeyEmptyFolds:
    """per_key_oos_stability returns empty results for no folds."""

    def test_per_key_empty(self):
        from a_share_agent.backtest.oos_stability import per_key_oos_stability
        result = per_key_oos_stability([])
        assert result["n_keys"] == 0
        assert result["keys"] == {}
        assert result["classification_summary"] == {}


# ====== AS14: Four-key extraction ======
class TestFourKeyExtraction:
    """_four_key_from_trade handles missing fields."""

    def test_four_key_from_trade_with_defaults(self):
        from a_share_agent.backtest.oos_stability import _four_key_from_trade
        trade = {"regime_at_signal": "BULL", "theme_lifecycle": "ACTIVE",
                 "pattern_id": "P1", "pattern_version": "v1"}
        key = _four_key_from_trade(trade)
        assert key == ("BULL", "ACTIVE", "P1", "v1")

    def test_four_key_fills_unknown(self):
        from a_share_agent.backtest.oos_stability import _four_key_from_trade
        trade = {}
        key = _four_key_from_trade(trade)
        assert key == ("UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN")


# ====== AS15: _json_safe ======
class TestJsonSafe:
    """_json_safe removes non-finite floats from dicts."""

    def test_json_safe_finite(self):
        from a_share_agent.backtest.oos_stability import _json_safe
        d = {"a": 1.0, "b": -0.5, "c": None}
        _json_safe(d)
        assert d["a"] == 1.0

    def test_json_safe_replaces_nan(self):
        from a_share_agent.backtest.oos_stability import _json_safe
        import math
        d = {"a": float("nan")}
        _json_safe(d)
        assert d["a"] is None

    def test_json_safe_replaces_inf(self):
        from a_share_agent.backtest.oos_stability import _json_safe
        import math
        d = {"a": float("inf")}
        _json_safe(d)
        assert d["a"] is None


@pytest.fixture
def producer_runner(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from a_share_agent.backtest import walk_forward_service as service
    from test_oos_stability import integrity_cohort
    reports = integrity_cohort()
    plan = [{"fold_id": i, "train_start": "2020-01-01", "train_end_exclusive": "2021-01-01",
             "test_start": "2021-01-01", "test_end_exclusive": "2021-04-01", "complete": True}
            for i in range(4)]
    monkeypatch.setattr(service, "WalkForwardConfig", lambda **kwargs: SimpleNamespace(
        stability_thresholds={}, universe=(), warmup_bars=kwargs.get("warmup_bars", 260), canonical_hash=lambda: "wfhash", to_dict=lambda: {}))
    monkeypatch.setattr(service, "generate_folds", lambda cfg: (plan, None))
    monkeypatch.setattr(service, "HistoricalDataProvider", lambda *a, **k: SimpleNamespace(
        load_universe_for_period=lambda *a, **k: SimpleNamespace(symbols=["X"])))
    monkeypatch.setattr(service, "settings_from", lambda *a, **k: SimpleNamespace(
        universe_file="u.csv", max_universe=10, universe_mode="configured"))
    monkeypatch.setattr(service, "preflight_fold", lambda *a, **k: ("READY", True, [], {}))
    monkeypatch.setattr(service, "BacktestEngine", lambda *a, **k: SimpleNamespace(
        run=lambda *a, **k: reports[int(k["evaluation_window"]["fold_id"])]))
    monkeypatch.setattr(service, "get_git_metadata", lambda *a, **k: {"git_commit_sha": "source"})
    monkeypatch.setattr(service, "_compute_rule_hashes", lambda *a, **k: {})
    def run(**overrides):
        result = service.run_stability(
            SimpleNamespace(project_root=tmp_path, defaults={"benchmarks": {}}, config_hash="c"),
            {"start_date": "2020-01-01", "end_date": "2021-04-01", **overrides}, tmp_path / "run", "integrity")
        return result, json.loads((tmp_path / "run" / "oos_per_key.json").read_text()), tmp_path / "run"
    return reports, plan, run


@pytest.mark.parametrize("returned_id", [99, None])
def test_service_engine_fold_id_mismatch_fails_closed(producer_runner, returned_id):
    reports, plan, run = producer_runner
    reports[0]["fold_id"] = returned_id
    result, artifact, directory = run()
    assert result["failed"] == 1
    assert result["stability_classification"] != "STABLE_CANDIDATE"
    assert all(key["classification"] != "STABLE_CANDIDATE" for key in artifact["keys"].values())
    methodology = json.loads((directory / "methodology.json").read_text())
    assert str(returned_id) in methodology["fold_errors"][0]["error"]


@pytest.mark.parametrize("status,complete", [("RUN_FAILED", False), ("DATA_BLOCKED", False),
                                             ("PARTIAL", True), ("COMPLETED", False)])
def test_service_bad_planned_fold_prevents_stability(producer_runner, status, complete):
    reports, plan, run = producer_runner
    # Retain four successful folds, so the sample-size gate alone cannot protect us.
    plan.append({**plan[0], "fold_id": 4})
    reports.append({"fold_id": 4, "complete": complete, "status": status, "trades": []})
    result, artifact, directory = run()
    assert result["status"] == "PARTIAL"
    assert result["stability_classification"] != "STABLE_CANDIDATE"
    assert all(key["classification"] != "STABLE_CANDIDATE" for key in artifact["keys"].values())


def test_service_repeated_planned_fold_fails_closed(producer_runner):
    reports, plan, run = producer_runner
    plan.append(dict(plan[0]))
    result, artifact, directory = run()
    assert result["stability_classification"] != "STABLE_CANDIDATE"
    assert result["status"] != "COMPLETED"


def test_manifest_without_completion_marker_is_incomplete(tmp_path):
    from a_share_agent.backtest.walk_forward_service import _is_incomplete_run
    (tmp_path / "manifest.json").write_text('{"overall_status":"COMPLETED"}')
    assert _is_incomplete_run(tmp_path)


def test_json_replace_failure_preserves_previous_bytes(tmp_path, monkeypatch):
    from a_share_agent.backtest import walk_forward_persistence as persistence
    path = tmp_path / "artifact.json"
    path.write_text('{"old":true}')
    def fail(*args):
        raise OSError("interrupted publication")
    monkeypatch.setattr(__import__("os"), "replace", fail)
    with pytest.raises(OSError, match="interrupted"):
        persistence.write_finite_json(path, {"new": True})
    assert path.read_text() == '{"old":true}'


def test_service_persists_consumed_universe_and_unverifiable_identity(producer_runner):
    from a_share_agent.backtest.walk_forward_service import _stable_hash
    reports, plan, run = producer_runner
    result, artifact, directory = run()
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["consumed_universes"]["0"]["symbols"] == ["X"]
    assert manifest["consumed_universes"]["0"]["universe_sha256"] == _stable_hash(["X"])
    assert manifest["physical_provenance"]["status"] == "UNVERIFIABLE"
    assert manifest["generation_id"] == artifact["input_manifest_binding"]["generation_id"]
    assert (directory / "completion.json").exists()


def test_failed_rerun_preserves_completed_generation(producer_runner):
    reports, plan, run = producer_runner
    result, artifact, directory = run()
    before = {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    reports[0]["fold_id"] = 99
    failed, _, _ = run()
    assert failed["status"] != "COMPLETED"
    assert before == {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def test_interrupted_generation_is_never_published(producer_runner, monkeypatch):
    from a_share_agent.backtest import walk_forward_service as service
    reports, plan, run = producer_runner
    result, artifact, directory = run()
    latest = (directory.parent / "latest.json").read_bytes()
    original = service._write_finite_json
    def interrupt(path, data):
        if path.name == "completion.json":
            raise OSError("crash before completion")
        original(path, data)
    monkeypatch.setattr(service, "_write_finite_json", interrupt)
    with pytest.raises(OSError, match="crash before completion"):
        run()
    assert (directory.parent / "latest.json").read_bytes() == latest
    assert json.loads((directory / "oos_per_key.json").read_text()) == artifact


@pytest.mark.parametrize("mutation", ["missing_marker", "manifest", "artifact", "fold", "escape", "symlink"])
def test_published_generation_tampering_fails_closed(producer_runner, mutation):
    from a_share_agent.backtest.oos_stability import verify_per_key_oos_artifact
    reports, plan, run = producer_runner
    result, artifact, directory = run()
    manifest_path = directory / "manifest.json"
    if mutation == "missing_marker":
        (directory / "completion.json").unlink()
    elif mutation == "manifest":
        manifest_path.write_text('{}')
    elif mutation == "artifact":
        (directory / "oos_per_key.json").write_text('{}')
    elif mutation == "fold":
        (directory / "folds/0/report.json").write_text('{}')
    elif mutation == "escape":
        manifest = json.loads(manifest_path.read_text())
        manifest["output_files"]["../outside.json"] = "bogus"
        manifest_path.write_text(json.dumps(manifest))
    else:
        target = directory / "folds/0/report.json"
        backup = directory.parent / "outside.json"
        backup.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(backup)
    verified = verify_per_key_oos_artifact(artifact, request_key=("BULL", "ACTIVE", "MORNING_STAR", "v1"), manifest_path=manifest_path)
    assert verified["key_eligible"] is False
    assert any("manifest" in reason for reason in verified["reasons"])


def test_service_preserves_universe_order_duplicates_and_declared_keys(producer_runner, monkeypatch):
    from types import SimpleNamespace
    from a_share_agent.backtest import walk_forward_service as service
    from a_share_agent.backtest.oos_stability import _key_to_artifact_key
    reports, plan, run = producer_runner
    monkeypatch.setattr(service, "HistoricalDataProvider", lambda *a, **k: SimpleNamespace(
        load_universe_for_period=lambda *a, **k: SimpleNamespace(
            symbols=["Z", "X", "Z"], source="fixture-source", dataset_version="fixture-v1", point_in_time=True)))
    consumed = []
    monkeypatch.setattr(service, "BacktestEngine", lambda *a, **k: SimpleNamespace(
        run=lambda symbols, **k: (consumed.append(list(symbols)) or reports[int(k["evaluation_window"]["fold_id"])])))
    declared = [("BEAR", "DECLINING", "NO_TRADE_PATTERN", "v1")]
    result, artifact, directory = run(declared_keys=declared)
    manifest = json.loads((directory / "manifest.json").read_text())
    assert consumed == [["Z", "X", "Z"]] * 4
    universe = manifest["consumed_universes"]["0"]
    assert universe["symbols"] == consumed[0]
    assert universe["universe_sha256"] == service._stable_hash(consumed[0])
    assert universe["source"] == "fixture-source"
    assert universe["dataset_version"] == "fixture-v1"
    assert artifact["keys"][_key_to_artifact_key(declared[0])]["classification"] == "INSUFFICIENT_DATA"


def test_completed_rerun_publishes_new_generation_without_mutating_old(producer_runner):
    reports, plan, run = producer_runner
    first, artifact, original = run()
    before = (original / "completion.json").read_bytes()
    second, _, _ = run()
    generation = Path(second["output_dir"])
    assert generation != original
    assert (original / "completion.json").read_bytes() == before
    pointer = json.loads((original.parent / "latest.json").read_text())
    assert pointer["path"] == str(generation)
    assert pointer["generation_id"] == json.loads((generation / "manifest.json").read_text())["generation_id"]


def test_partial_rerun_does_not_replace_latest_pointer(producer_runner):
    reports, plan, run = producer_runner
    result, artifact, directory = run()
    pointer = (directory.parent / "latest.json").read_bytes()
    reports[0]["fold_id"] = 99
    failed, _, _ = run()
    failed_directory = Path(failed["output_dir"])
    assert failed_directory != directory
    assert (failed_directory / "diagnostics.json").exists()
    assert not (failed_directory / "completion.json").exists()
    assert (directory.parent / "latest.json").read_bytes() == pointer


@pytest.mark.parametrize("manifest,marker", [("[]", "{}"), ("{}", "[]"), ("null", "null")])
def test_malformed_publication_metadata_is_incomplete(tmp_path, manifest, marker):
    from a_share_agent.backtest.walk_forward_service import _is_incomplete_run
    (tmp_path / "manifest.json").write_text(manifest)
    (tmp_path / "completion.json").write_text(marker)
    assert _is_incomplete_run(tmp_path)


def test_service_binds_exact_ledger_and_fold_events(producer_runner, monkeypatch):
    from types import SimpleNamespace
    from a_share_agent.backtest import walk_forward_service as service
    from a_share_agent.backtest.data import ConsumedInputLedger
    reports, plan, run = producer_runner
    def provider(root, **kwargs):
        ledger = kwargs['ledger']
        path = root / 'consumed.csv'
        path.write_text('symbol\nX\n')
        def universe(*args, **kw):
            ledger.read(path, kind='universe')
            return SimpleNamespace(symbols=['X'])
        return SimpleNamespace(ledger=ledger, load_universe_for_period=universe)
    monkeypatch.setattr(service, 'HistoricalDataProvider', provider)
    result, artifact, directory = run()
    manifest = json.loads((directory / 'manifest.json').read_text())
    events = manifest['consumed_input_ledger']['events']
    assert manifest['physical_provenance']['status'] == 'VERIFIED'
    assert manifest['consumed_input_ledger_sha256'] == ConsumedInputLedger.digest(events)
    assert manifest['physical_inputs'] == ConsumedInputLedger.physical_inputs(events)
    assert artifact['input_manifest_binding']['consumed_input_ledger_sha256'] == manifest['consumed_input_ledger_sha256']
    for fold in plan:
        saved = json.loads((directory / f"folds/{fold['fold_id']}/consumed_inputs.json").read_text())
        assert saved['events'] == [e for e in events if e['fold_id'] == str(fold['fold_id'])]
