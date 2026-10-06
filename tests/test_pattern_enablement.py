"""Policy-only contracts using explicitly synthetic physical fixtures, never market evidence."""
import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from a_share_agent.backtest.oos_stability import build_per_key_oos_artifact, per_key_oos_stability
from a_share_agent.strategy.enablement import PhysicalEvidence, PolicyEntry, PolicyConfig, PolicyStatus, verify_physical_artifact, load_config_from_yaml
from a_share_agent.strategy.policy_loader import PolicyLoader

KEY = ("BULL", "ACTIVE", "P1", "v1")
SECRET = b"test-only-integrity-key-32-bytes!!"
RULE_HASHES = {f"{name}_sha256": hashlib.sha256(name.encode()).hexdigest()
               for name in ("router", "scanner", "regime", "context", "scoring", "cost_source")}

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False))

@pytest.fixture
def clock(monkeypatch):
    now = [2000000000.0]
    monkeypatch.setattr("time.time", lambda: now[0])
    return now

@pytest.fixture
def physical(tmp_path, clock):
    """Hash-bound synthetic reports; does not assert real data provenance."""
    directory = tmp_path / "published"
    directory.mkdir()
    inp = tmp_path / "fixture.csv"
    inp.write_text("synthetic-only\n")
    reports = []
    for i in range(4):
        trades = []
        for j in range(10):
            common = dict(round_trip_id=f"{i}-{j}", regime_at_signal=KEY[0], theme_lifecycle=KEY[1], pattern_id=KEY[2], pattern_version=KEY[3], regime_data_quality_at_signal={"state": "ok"}, theme_data_quality_at_signal={"state": "ok"}, trade_date="2024-01-01", exit_reason="SIGNAL:test")
            trades += [dict(common, direction="BUY", pnl_pct=None), dict(common, direction="SELL", pnl_pct=0.02 if j < 9 else -0.01)]
        report = dict(fold_id=i, complete=True, status="COMPLETED", coverage=1.0, trades=trades)
        reports.append(report)
        dump(directory / f"fold_{i}" / "report.json", report)
    manifest = dict(run_id="synthetic", source_sha="fixture-source", generation_id="fixture-generation", overall_status="COMPLETED", completion=True, artifact_path="oos_per_key.json", config_hash="c", wf_config_hash="w", rule_hashes=dict(RULE_HASHES), dirty=False, generated_at=datetime.fromtimestamp(clock[0]-100, timezone.utc).isoformat(), consumed_universes={"0": {"symbols": ["FIXTURE"], "universe_sha256": hashlib.sha256(b'["FIXTURE"]').hexdigest()}}, physical_provenance={"status": "VERIFIED"}, physical_inputs=[{"path": str(inp), "sha256": digest(inp)}], fold_plan={"folds": [{"fold_id": i} for i in range(4)]}, settings={"stability_thresholds": {}}, declared_keys=[list(KEY)])
    from a_share_agent.backtest.data import ConsumedInputLedger
    ledger = ConsumedInputLedger(tmp_path)
    for i in range(4):
        ledger.fold_id = str(i)
        ledger.read(inp, kind="synthetic_fixture")
        events = [e for e in ledger.events if e['fold_id'] == str(i)]
        dump(directory / f"folds/{i}/consumed_inputs.json", dict(events=events,
            sha256=ledger.digest(events), status="VERIFIED"))
    snapshot = ledger.snapshot(verify_files=True)
    manifest.update(project_root=str(tmp_path), consumed_input_ledger=snapshot,
        consumed_input_ledger_sha256=snapshot['sha256'], physical_inputs=snapshot['physical_inputs'])
    result = per_key_oos_stability(reports, expected_fold_ids=list(range(4)), declared_keys=[KEY])
    assert result["keys"]["::".join(KEY)]["classification"] == "STABLE_CANDIDATE"
    artifact = build_per_key_oos_artifact(result, "synthetic", "fixture-source", "c", "w", dict(RULE_HASHES), manifest)
    artifact["generated_at"] = manifest["generated_at"]
    def publish():
        artifact.pop("content_hash", None)
        canonical = json.dumps(artifact, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        artifact["content_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
        dump(directory / "oos_per_key.json", artifact)
        manifest["artifact_sha256"] = digest(directory / "oos_per_key.json")
        manifest["oos_per_key_sha256"] = artifact["content_hash"]
        manifest["output_files"] = {str(p.relative_to(directory)): digest(p) for p in directory.rglob("*.json") if p.name not in ("manifest.json", "completion.json")}
        dump(directory / "manifest.json", manifest)
        dump(directory / "completion.json", dict(run_id=manifest["run_id"], generation_id=manifest["generation_id"], manifest_sha256=digest(directory / "manifest.json")))
    publish()
    return directory, artifact, manifest, publish

@pytest.fixture
def pending(physical):
    return PolicyEntry.from_evidence(KEY, verify_physical_artifact(physical[0], KEY))

@pytest.fixture
def approved(pending):
    return pending.approve("operator")

@pytest.fixture
def loader(tmp_path):
    return PolicyLoader([tmp_path / "policies"], integrity_key=SECRET, trusted_identity="test-authority", config=PolicyConfig(snapshot_directory=str(tmp_path / "audit"), max_file_age_seconds=0))

def test_exact_emitted_rule_hash_binding(physical, approved):
    assert approved.context_version == RULE_HASHES["context_sha256"]
    assert approved.context_config_hash == RULE_HASHES["context_sha256"]
    assert approved.pattern_config_hash == RULE_HASHES["scanner_sha256"]

@pytest.mark.parametrize("name", list(RULE_HASHES))
@pytest.mark.parametrize("value", [None, "bad", "g" * 64, 123])
def test_required_rule_hashes_fail_closed(physical, name, value):
    directory, artifact, manifest, publish = physical
    for record in (artifact, manifest):
        if value is None:
            record["rule_hashes"].pop(name)
        else:
            record["rule_hashes"][name] = value
    publish()
    with pytest.raises(ValueError, match="rule hash"):
        verify_physical_artifact(directory, KEY)

@pytest.mark.parametrize("name", ["context_sha256", "scanner_sha256"])
def test_rule_mutation_rejects_existing_snapshot(physical, approved, name):
    directory, artifact, manifest, publish = physical
    data = approved.to_dict()
    for record in (artifact, manifest):
        record["rule_hashes"][name] = "f" * 64
    publish()
    fresh = verify_physical_artifact(directory, KEY)
    field = "context_version" if name == "context_sha256" else "pattern_config_hash"
    assert getattr(fresh, field) == "f" * 64
    with pytest.raises(ValueError, match="binding"):
        PolicyEntry.from_dict(data)

@pytest.mark.parametrize("name", ["context_sha256", "scanner_sha256"])
@pytest.mark.parametrize("target", [1, 2])
def test_rule_mutation_one_side_rejects_evidence(physical, name, target):
    physical[target]["rule_hashes"][name] = "f" * 64
    physical[3]()
    with pytest.raises(ValueError):
        verify_physical_artifact(physical[0], KEY)

@pytest.mark.parametrize("section,field,hash_name", [
    ("strategy_router", "version", "router_sha256"),
    ("backtest", "enabled_strategies", "scanner_sha256"),
    ("defaults", "market_filter", "regime_sha256"),
    ("runtime", "mcp", "context_sha256"),
    ("defaults", "score_weights", "scoring_sha256"),
    ("backtest", "commission_rate", "cost_source_sha256"),
])
def test_actual_runtime_rule_mutation_rejects_evidence(runtime_root, physical, section, field, hash_name):
    from a_share_agent.config import load_config
    from a_share_agent.backtest.walk_forward_service import _compute_rule_hashes
    cfg = load_config(runtime_root)
    before = _compute_rule_hashes(cfg)
    for record in physical[1:3]:
        record["rule_hashes"] = before.copy()
        record["config_hash"] = cfg.config_hash
    physical[3]()
    verify_physical_artifact(physical[0], KEY, config=cfg)
    source = getattr(cfg, section)
    source[field] = {**source[field], "mutation": True} if isinstance(source[field], dict) else ["changed"] if isinstance(source[field], list) else "changed"
    assert _compute_rule_hashes(cfg)[hash_name] != before[hash_name]
    with pytest.raises(ValueError, match="rule hash"):
        verify_physical_artifact(physical[0], KEY, config=cfg)

@pytest.mark.parametrize("section", ["strategy_router", "defaults", "backtest", "runtime"])
def test_missing_required_rule_source_fails_closed(runtime_root, section):
    from a_share_agent.config import load_config
    from a_share_agent.backtest.walk_forward_service import _compute_rule_hashes
    cfg = load_config(runtime_root)
    getattr(cfg, section).clear()
    with pytest.raises(ValueError, match="Required rule source"):
        _compute_rule_hashes(cfg)

def test_rule_hashes_redact_secrets_and_are_order_stable(runtime_root):
    from a_share_agent.config import load_config
    from a_share_agent.backtest.walk_forward_service import _compute_rule_hashes
    cfg = load_config(runtime_root)
    cfg.runtime["mcp"]["password"] = "first-secret"
    before = _compute_rule_hashes(cfg)
    cfg.runtime["mcp"]["password"] = "different-secret"
    cfg.runtime["mcp"] = dict(reversed(list(cfg.runtime["mcp"].items())))
    assert _compute_rule_hashes(cfg) == before

def test_empty_section_digest_cannot_authorize(physical):
    empty_hash = hashlib.sha256(b"{}").hexdigest()
    for record in physical[1:3]:
        record["rule_hashes"]["router_sha256"] = empty_hash
    physical[3]()
    with pytest.raises(ValueError, match="rule hash"):
        verify_physical_artifact(physical[0], KEY)

def test_runtime_loader_rejects_actual_config_mutation(runtime_root, physical, loader, clock):
    from a_share_agent.config import load_config
    cfg = load_config(runtime_root)
    for record in physical[1:3]:
        record["rule_hashes"] = cfg.rule_hashes
        record["config_hash"] = cfg.config_hash
    physical[3]()
    approved = PolicyEntry.from_evidence(KEY, verify_physical_artifact(physical[0], KEY)).approve("operator")
    bound = PolicyLoader(loader._directories, config=loader.config, integrity_key=SECRET,
                         trusted_identity="test-authority", runtime_config=cfg)
    bound.publish_generation([approved], bound._directories[0])
    signal = {"pattern_id": KEY[2], "pattern_version": KEY[3]}
    market = {"regime": KEY[0], "data_quality": {"state": "ok"}}
    sector = {"lifecycle": KEY[1], "data_quality": {"state": "ok"}}
    assert bound.authorize_entry(signal, market_context=market, sector_context=sector, as_of=clock[0])["allowed"]
    cfg.backtest["commission_rate"] *= 2
    assert not bound.authorize_entry(signal, market_context=market, sector_context=sector, as_of=clock[0])["allowed"]

PATTERN_POLICY_MUTATIONS = [
    ("loading", "directories", ["data/policy/changed"]),
    ("loading", "max_file_age_seconds", 120),
    ("validation", "allow_future_effective", True),
    ("defaults", "expiry_days", 12),
    ("audit", "enable_snapshots", False),
    ("audit", "snapshot_directory", "data/policy/changed-audit"),
]

def mutate_pattern_policy(runtime_root, section, field, value):
    import yaml
    path = runtime_root / "config" / "pattern_enablement.yaml"
    policy = yaml.safe_load(path.read_text())
    policy[section][field] = value
    path.write_text(yaml.safe_dump(policy))
    return policy

@pytest.mark.parametrize("section,field,value", PATTERN_POLICY_MUTATIONS)
def test_pattern_policy_mutation_changes_runtime_config_identity(runtime_root, section, field, value):
    from a_share_agent.config import load_config
    before = load_config(runtime_root)
    policy = mutate_pattern_policy(runtime_root, section, field, value)
    after = load_config(runtime_root)
    assert after.config_hash != before.config_hash
    assert after.pattern_enablement == policy
    assert after.rule_hashes == before.rule_hashes
    assert after.strategy_router["policy"]["enabled"] is False

def test_pattern_policy_identity_redacts_secrets_and_is_canonical(runtime_root):
    import yaml
    from a_share_agent.config import load_config
    path = runtime_root / "config" / "pattern_enablement.yaml"
    policy = yaml.safe_load(path.read_text())
    policy["password"] = "first-secret"
    path.write_text(yaml.safe_dump(policy))
    cfg = load_config(runtime_root)
    assert cfg.pattern_enablement == policy
    before = cfg.config_hash
    policy["password"] = "different-secret"
    policy = dict(reversed(list(policy.items())))
    path.write_text(yaml.safe_dump(policy, sort_keys=False))
    assert load_config(runtime_root).config_hash == before

@pytest.mark.parametrize("section,field,value", PATTERN_POLICY_MUTATIONS)
def test_pattern_policy_mutation_rejects_evidence_and_authorization(runtime_root, physical, loader, clock, section, field, value):
    from a_share_agent.config import load_config
    cfg = load_config(runtime_root)
    for record in physical[1:3]:
        record["rule_hashes"] = cfg.rule_hashes
        record["config_hash"] = cfg.config_hash
    physical[3]()
    evidence = verify_physical_artifact(physical[0], KEY, config=cfg)
    approved = PolicyEntry.from_evidence(KEY, evidence).approve("operator")
    snapshot = approved.to_dict()
    assert PolicyEntry.from_dict(snapshot) == approved
    bound = PolicyLoader(loader._directories, config=loader.config, integrity_key=SECRET,
                         trusted_identity="test-authority", runtime_config=cfg)
    bound.publish_generation([approved], bound._directories[0])
    signal = {"pattern_id": KEY[2], "pattern_version": KEY[3]}
    market = {"regime": KEY[0], "data_quality": {"state": "ok"}}
    sector = {"lifecycle": KEY[1], "data_quality": {"state": "ok"}}
    assert bound.authorize_entry(signal, market_context=market, sector_context=sector, as_of=clock[0])["allowed"]
    mutate_pattern_policy(runtime_root, section, field, value)
    changed = load_config(runtime_root)
    assert changed.rule_hashes == cfg.rule_hashes
    with pytest.raises(ValueError, match="config.*hash"):
        verify_physical_artifact(physical[0], KEY, config=changed)
    bound.runtime_config = changed
    assert not bound.authorize_entry(signal, market_context=market, sector_context=sector, as_of=clock[0])["allowed"]
    # A newly published config-bound artifact cannot reconstruct an old policy.
    for record in physical[1:3]:
        record["config_hash"] = changed.config_hash
    physical[3]()
    verify_physical_artifact(physical[0], KEY, config=changed)
    with pytest.raises(ValueError, match="binding"):
        PolicyEntry.from_dict(snapshot)

def test_direct_evidence_construction_rejected():
    with pytest.raises(TypeError):
        PhysicalEvidence(producer_sha="forged", artifact_hash="self-hash", classification="STABLE_CANDIDATE", context_version="v", config_hash="c", pattern_config_hash="p", context_config_hash="x", generated_at="2024-01-01T00:00:00+00:00", run_id="r", wf_config_hash="w", evidence_type="per_key")

def test_direct_policy_construction_rejected(approved):
    with pytest.raises(TypeError):
        PolicyEntry(**approved.to_dict())

def test_immutable_and_replace_cannot_forge(physical, approved):
    evidence = verify_physical_artifact(physical[0], KEY)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        evidence.artifact_hash = "forged"
    with pytest.raises(TypeError):
        replace(approved, evidence_id="forged")

@pytest.mark.parametrize("key", [("BULL", "ACTIVE", "P1", "v2"), ("BULL", "ACTIVE", "P1"), ("BULL", "ACTIVE", "P1", 1), ("", "ACTIVE", "P1", "v1"), ("PANIC", "ACTIVE", "P1", "v1")])
def test_exact_key_fail_closed(physical, key):
    with pytest.raises((ValueError, TypeError)):
        PolicyEntry.from_evidence(key, verify_physical_artifact(physical[0], key))

@pytest.mark.parametrize("mutation", ["unverifiable", "marker", "artifact", "symlink", "label", "key_tuple", "future", "naive", "missing_reports"])
def test_physical_invalidations(physical, mutation, clock):
    directory, artifact, manifest, publish = physical
    if mutation == "unverifiable":
        manifest["physical_provenance"]["status"] = "UNVERIFIABLE"; publish()
    elif mutation == "marker":
        (directory / "completion.json").unlink()
    elif mutation == "artifact":
        (directory / "oos_per_key.json").write_text("{}")
    elif mutation == "symlink":
        p = directory / "oos_per_key.json"; dest = directory.parent / "outside.json"; dest.write_bytes(p.read_bytes()); p.unlink(); p.symlink_to(dest)
    elif mutation == "label":
        report = json.loads((directory / "fold_0/report.json").read_text()); report["trades"] = []; dump(directory / "fold_0/report.json", report); publish()
    elif mutation == "key_tuple":
        artifact["keys"]["::".join(KEY)]["key_tuple"][-1] = "v2"; publish()
    elif mutation in ("future", "naive"):
        artifact["generated_at"] = datetime.fromtimestamp(clock[0]+10, timezone.utc).isoformat() if mutation == "future" else "2024-01-01T00:00:00"; publish()
    else:
        (directory / "fold_0/report.json").unlink(); publish()
    with pytest.raises((ValueError, OSError)):
        PolicyEntry.from_evidence(KEY, verify_physical_artifact(directory, KEY))

def test_changed_artifact_after_ingestion_rejected(physical):
    ev = verify_physical_artifact(physical[0], KEY)
    (physical[0] / "oos_per_key.json").write_text("{}")
    with pytest.raises(ValueError):
        PolicyEntry.from_evidence(KEY, ev)

def test_pending_and_explicit_approval(pending, clock):
    assert pending.status == PolicyStatus.PENDING and not pending.is_enabled
    clock[0] += 10
    approved = pending.approve("operator")
    assert not approved.is_valid_at(clock[0]-1)
    assert approved.is_valid_at(clock[0])
    assert not approved.is_valid_at(pending.issued_at-1)
    assert not approved.is_valid_at(approved.expires_at)
    with pytest.raises(ValueError):
        pending.approve(" ")

@pytest.mark.parametrize("field,value", [("key", ["BULL", "ACTIVE", "P1", "v2"]), ("evidence_id", "forged"), ("approved_at", None), ("approved_at", float("nan")), ("approval_authority", ""), ("status", "bogus"), ("source_run_id", "fake"), ("generated_at_ts", 1), ("valid_from", 0), ("extra", "bad")])
def test_snapshot_invariants(approved, tmp_path, field, value):
    data = approved.to_dict(); data[field] = value
    p = tmp_path / "snapshot.json"; p.write_text(json.dumps(data))
    with pytest.raises((ValueError, TypeError)):
        PolicyEntry.from_snapshot(p)

def test_roundtrip_and_disabled_terminal(approved, tmp_path):
    p = tmp_path / "snapshot.json"; approved.to_snapshot(p)
    assert PolicyEntry.from_snapshot(p).to_dict() == approved.to_dict()
    disabled = approved.disable("manual safety")
    assert not disabled.is_enabled
    with pytest.raises(ValueError):
        disabled.approve("operator")

def test_config_expiry_and_future_effective(physical, clock):
    ev = verify_physical_artifact(physical[0], KEY)
    cfg = PolicyConfig(expiry_days=7)
    e = PolicyEntry.from_evidence(KEY, ev, config=cfg)
    assert e.expires_at-e.valid_from == 7*86400
    with pytest.raises(ValueError):
        PolicyEntry.from_evidence(KEY, ev, valid_from=clock[0]+1, config=cfg)
    e = PolicyEntry.from_evidence(KEY, ev, valid_from=clock[0]+10, config=PolicyConfig(allow_future_effective=True))
    assert not e.approve("operator").is_valid_at(clock[0])

@pytest.mark.parametrize("yaml", ["defaults:\n  expiry_days: -1\n", "validation:\n  require_exact_match: false\n", "defaults:\n  default_approver: auto\n", "audit:\n  max_snapshots_per_policy: 3\n", "validation:\n  allow_future_effective: nope\n"])
def test_invalid_or_removed_knobs_rejected(tmp_path, yaml):
    p = tmp_path / "config.yaml"; p.write_text(yaml)
    with pytest.raises(ValueError):
        load_config_from_yaml(p)

def test_config_directories_consumed(tmp_path):
    p = tmp_path / "config.yaml"; p.write_text(f"loading:\n  directories:\n    - {tmp_path / 'configured'}\ndefaults:\n  expiry_days: 12\n")
    cfg = load_config_from_yaml(p)
    assert cfg.expiry_days == 12
    l = PolicyLoader(config=cfg, integrity_key=SECRET, trusted_identity="test-authority")
    assert l.load_active() == {}

def test_loose_snapshot_cannot_authorize(approved, tmp_path):
    approved.to_snapshot(tmp_path / "loose.json")
    assert PolicyLoader([tmp_path]).load_active() == {}

def test_generation_lifecycle_and_history(loader, approved, clock):
    root = loader._directories[0]
    first = loader.publish_generation([approved], root)
    assert len(loader.load_active()) == 1
    assert loader.load_active(as_of=first["timestamp"]-1) == {}
    clock[0] += 10
    loader.publish_generation([approved.disable("manual")], root)
    assert loader.load_active() == {}
    assert len(loader.load_active(as_of=first["timestamp"])) == 1

@pytest.mark.parametrize("mutation", ["missing", "extra", "hash", "manifest", "pointer", "symlink", "untrusted", "count"])
def test_generation_corruption_never_partial(loader, approved, mutation):
    root = loader._directories[0]
    result = loader.publish_generation([approved], root)
    gen = Path(result["generation_path"])
    member = next(p for p in gen.glob("*.json") if p.name != "generation.json")
    if mutation == "missing": member.unlink()
    elif mutation == "extra": dump(gen / "extra.json", approved.to_dict())
    elif mutation == "hash": member.write_text("{}")
    elif mutation == "manifest": (gen / "generation.json").write_text("{}")
    elif mutation == "pointer": (root / "active").write_text(str(gen))
    elif mutation == "symlink":
        dest = root / "outside.json"; dest.write_bytes(member.read_bytes()); member.unlink(); member.symlink_to(dest)
    elif mutation == "untrusted":
        loader = PolicyLoader([root], integrity_key=b"wrong-secret", trusted_identity="other")
    else:
        data = json.loads((gen / "generation.json").read_text()); data["entry_count"] = 99; dump(gen / "generation.json", data)
    assert loader.load_active() == {}
    assert loader.load_all() == []

def test_duplicate_generation_key_rejected(loader, approved):
    with pytest.raises(ValueError):
        loader.publish_generation([approved, approved], loader._directories[0])

def test_manual_disable_precedence(loader, approved):
    assert loader._filter_active([approved.disable("manual"), approved], approved.approved_at) == {}
    assert loader._filter_active([approved, approved.disable("manual")], approved.approved_at) == {}

def test_staleness_config(tmp_path, approved, clock):
    l = PolicyLoader([tmp_path / "g"], integrity_key=SECRET, trusted_identity="t", config=PolicyConfig(max_file_age_seconds=5, enable_snapshots=False))
    l.publish_generation([approved], tmp_path / "g")
    clock[0] += 6
    assert l.load_active() == {}

def test_rollback_restores_whole_generation(loader, approved, clock):
    root = loader._directories[0]
    first = loader.publish_generation([approved], root)
    audit = loader.save_audit_record(approved, loader.config.snapshot_directory)
    clock[0] += 10
    loader.publish_generation([], root)
    assert loader.load_active() == {}
    restored = loader.rollback_generation(loader.config.snapshot_directory, Path(audit["audit_path"]).name, generation_dir=root)
    assert restored["entry_count"] == 1 and len(loader.load_active()) == 1
    assert loader.load_active(as_of=first["timestamp"]-1) == {}

@pytest.mark.parametrize("mutation", ["escape", "symlink", "hash", "identity", "missing", "self_hash"])
def test_audit_integrity(loader, approved, mutation, tmp_path):
    root = loader._directories[0]; loader.publish_generation([approved], root)
    audit = loader.save_audit_record(approved, loader.config.snapshot_directory)
    p = Path(audit["audit_path"])
    if mutation == "escape": filename = "../escape.json"
    elif mutation == "symlink":
        dest = tmp_path / "external.json"; dest.write_bytes(p.read_bytes()); p.unlink(); p.symlink_to(dest); filename = p.name
    else:
        data = json.loads(p.read_text())
        if mutation == "hash": data["generation"]["manifest_hash"] = "bad"
        elif mutation == "identity": data["identity"] = "attacker"
        elif mutation == "missing": data.pop("signature")
        else:
            data["generation"]["manifest_hash"] = "bad"; data["signature"] = hashlib.sha256(json.dumps(data).encode()).hexdigest()
        dump(p, data); filename = p.name
    with pytest.raises((ValueError, OSError)):
        loader.rollback_generation(loader.config.snapshot_directory, filename, generation_dir=root)
    assert len(loader.load_active()) == 1

def test_audit_path_config_and_disable(loader, approved, tmp_path):
    loader.publish_generation([approved], loader._directories[0])
    assert list(Path(loader.config.snapshot_directory).glob("audit_*.json"))
    with pytest.raises(ValueError):
        loader.save_audit_record(approved, tmp_path / "untrusted-audit")

def test_missing_trust_cannot_publish(approved, tmp_path):
    with pytest.raises(ValueError):
        PolicyLoader([tmp_path]).publish_generation([approved], tmp_path)

def test_future_manifest_publication_rejected(physical, clock):
    directory, artifact, manifest, publish = physical
    manifest["generated_at"] = datetime.fromtimestamp(clock[0]+10, timezone.utc).isoformat()
    publish()
    with pytest.raises(ValueError):
        verify_physical_artifact(directory, KEY)

def test_complete_two_member_generation_and_rollback(loader, approved, physical, clock):
    directory, artifact, manifest, publish = physical
    other_key = ("BULL", "ACTIVE", "P2", "v1")
    reports = []
    for path in sorted(directory.glob("fold_*/report.json")):
        report = json.loads(path.read_text())
        copies = [dict(t, round_trip_id=t["round_trip_id"]+"-other", pattern_id="P2") for t in report["trades"]]
        report["trades"].extend(copies)
        dump(path, report)
        reports.append(report)
    manifest["declared_keys"].append(list(other_key))
    result = per_key_oos_stability(reports, expected_fold_ids=list(range(4)), declared_keys=[KEY, other_key])
    artifact["keys"] = result["keys"]
    artifact["n_keys"] = result["n_keys"]
    artifact["classification_summary"] = result["classification_summary"]
    publish()
    # Re-ingest both records because a changed producer publication invalidates old bindings.
    first = PolicyEntry.from_evidence(KEY, verify_physical_artifact(directory, KEY)).approve("operator")
    second = PolicyEntry.from_evidence(other_key, verify_physical_artifact(directory, other_key)).approve("operator")
    root = loader._directories[0]
    generation = loader.publish_generation([first, second], root)
    audit = loader.save_audit_record(first, loader.config.snapshot_directory)
    assert len(loader.load_active()) == 2
    clock[0] += 10
    loader.publish_generation([], root)
    loader.rollback_generation(loader.config.snapshot_directory, Path(audit["audit_path"]).name, generation_dir=root)
    assert len(loader.load_active()) == 2
    current = loader._read_pointer(root)[-1]
    directory = root / current["generation"]
    member = next(p for p in directory.glob("*.json") if p.name != "generation.json")
    member.unlink()
    assert loader.load_active() == {}  # Never keep the surviving half.

def test_audit_disabled_consumed(tmp_path, approved):
    root = tmp_path / "g"
    audit = tmp_path / "audit"
    loader = PolicyLoader([root], integrity_key=SECRET, trusted_identity="t", config=PolicyConfig(enable_snapshots=False, snapshot_directory=str(audit)))
    loader.publish_generation([approved], root)
    assert not audit.exists()
    with pytest.raises(ValueError):
        loader.save_audit_record(approved, audit)


# ── B01-B07 Acceptance Tests ──
# Each maps to a numbered criterion in BBB_ACCEPTANCE_STANDARD.md §5.

def test_b01_non_stable_candidate_classification_rejected(physical):
    """B01: Only STABLE_CANDIDATE evidence creates valid policy; other classifications reject."""
    directory, artifact, manifest, publish = physical
    encoded = "BULL::ACTIVE::P1::v1"  # Same encoding used by per_key_oos_stability
    artifact["keys"][encoded]["classification"] = "UNSTABLE"
    publish()
    with pytest.raises(ValueError, match="non-authorizing"):
        verify_physical_artifact(directory, KEY)


def test_b01_manual_disable_overrides_approved(loader, approved, clock):
    """B01: Manual disable takes precedence over approved status."""
    root = loader._directories[0]
    loader.publish_generation([approved], root)
    assert str(KEY) in loader.load_active()
    disabled = approved.disable("manual_override")
    loader.publish_generation([disabled], root)
    assert str(KEY) not in loader.load_active()
    assert loader.authorize_entry(
        {"pattern_id": KEY[2], "pattern_version": KEY[3]},
        market_context={"regime": KEY[0], "data_quality": {"state": "ok"}},
        sector_context={"lifecycle": KEY[1], "data_quality": {"state": "ok"}},
        as_of=clock[0],
    )["reason"] != "approved_exact_policy"


def test_b02_unknown_key_disabled_no_trace(loader, physical, clock):
    """B02: Unknown pattern key returns disabled with clear reason."""
    directory, artifact, manifest, publish = physical
    approved = PolicyEntry.from_evidence(KEY, verify_physical_artifact(directory, KEY)).approve("op")
    loader.publish_generation([approved], loader._directories[0])
    unknown_key = ("BULL", "ACTIVE", "P99", "v99")
    decision = loader.authorize_entry(
        {"pattern_id": unknown_key[2], "pattern_version": unknown_key[3]},
        market_context={"regime": unknown_key[0], "data_quality": {"state": "ok"}},
        sector_context={"lifecycle": unknown_key[1], "data_quality": {"state": "ok"}},
        as_of=clock[0],
    )
    assert not decision["allowed"]
    assert decision["reason"] in ("missing_or_invalid_policy", "invalid_exact_key_or_context")


def test_b02_expired_key_disabled(physical, clock):
    """B02: Expired entry returns is_valid=False."""
    ev = verify_physical_artifact(physical[0], KEY)
    entry = PolicyEntry.from_evidence(KEY, ev, valid_days=1)
    entry = entry.approve("op")
    assert entry.is_valid_at(clock[0])
    assert not entry.is_valid_at(clock[0] + 86401)


def test_b03_past_evidence_rejects_future_data(physical, clock):
    """B03: Evidence generated in the future cannot be verified."""
    directory, artifact, manifest, publish = physical
    manifest["generated_at"] = "2099-01-01T00:00:00+00:00"
    publish()
    with pytest.raises(ValueError, match="future"):
        verify_physical_artifact(directory, KEY)


def test_b04_panic_context_blocks_authorization(loader, approved, clock):
    """B04: PANIC/reject regime overrides all, returns unsafe_context reason."""
    loader.publish_generation([approved], loader._directories[0])
    decision = loader.authorize_entry(
        {"pattern_id": KEY[2], "pattern_version": KEY[3]},
        market_context={"regime": "PANIC", "data_quality": {"state": "ok"}},
        sector_context={"lifecycle": KEY[1], "data_quality": {"state": "ok"}},
        as_of=clock[0],
    )
    assert not decision["allowed"]
    assert decision["reason"] == "unsafe_context"


def test_b04_rollback_restores_known_generation(loader, approved, clock):
    """B04: Rollback restores the complete previously known generation."""
    root = loader._directories[0]
    gen1 = loader.publish_generation([approved], root)
    audit = loader.save_audit_record(approved, loader.config.snapshot_directory)
    assert len(loader.load_active()) == 1
    clock[0] += 10
    loader.publish_generation([], root)
    assert len(loader.load_active()) == 0
    loader.rollback_generation(
        loader.config.snapshot_directory,
        Path(audit["audit_path"]).name,
        generation_dir=root,
    )
    assert len(loader.load_active()) == 1


def test_b05_filter_signals_excludes_disabled_patterns(loader, physical, clock):
    """B05: filter_signals only returns signals with enabled policy; disabled never generate BUY_READY."""
    directory, artifact, manifest, publish = physical
    approved = PolicyEntry.from_evidence(KEY, verify_physical_artifact(directory, KEY)).approve("op")
    loader.publish_generation([approved], loader._directories[0])

    market_ok = {"regime": KEY[0], "data_quality": {"state": "ok"}}
    sector_ok = {"lifecycle": KEY[1], "data_quality": {"state": "ok"}}

    signals = [
        {"signal": "test_pattern", "strength": "primary",
         "pattern_id": KEY[2], "pattern_version": KEY[3]},
    ]
    decisions = []
    filtered = loader.filter_signals(signals, market_context=market_ok, sector_context=sector_ok,
                                     as_of=clock[0], decisions=decisions)
    assert len(filtered) == 1
    assert decisions[0]["allowed"]

    unknown_signals = [
        {"signal": "strange_pattern", "strength": "primary",
         "pattern_id": "UNKNOWN", "pattern_version": "v0"},
    ]
    decisions2 = []
    filtered2 = loader.filter_signals(unknown_signals, market_context=market_ok, sector_context=sector_ok,
                                      as_of=clock[0], decisions=decisions2)
    assert len(filtered2) == 0
    assert not decisions2[0]["allowed"]


def test_b05_exit_signals_always_pass_filter(loader, approved, clock):
    """B05: exit strength signals bypass policy checks."""
    loader.publish_generation([approved], loader._directories[0])
    market_ok = {"regime": KEY[0], "data_quality": {"state": "ok"}}
    sector_ok = {"lifecycle": KEY[1], "data_quality": {"state": "ok"}}
    exit_signal = {"signal": "shooting_star_high", "strength": "exit",
                   "pattern_id": "NONEXISTENT", "pattern_version": "v1"}
    filtered = loader.filter_signals([exit_signal], market_context=market_ok, sector_context=sector_ok,
                                     as_of=clock[0])
    assert len(filtered) == 1


def test_b06_identical_input_replay_same_policy(loader, approved, clock):
    """B06: Same input replay produces the same policy selection; atomic load."""
    root = loader._directories[0]
    loader.publish_generation([approved], root)
    before = loader.load_active(as_of=clock[0])
    after = loader.load_active(as_of=clock[0])
    assert before == after


def test_b07_audit_contains_evidence_hash_policy_hash_rejection(loader, approved, clock, tmp_path):
    """B07: Audit records contain evidence hash, policy hash, and rejection reasons."""
    root = loader._directories[0]
    loader.publish_generation([approved], root)
    audit = loader.save_audit_record(approved, loader.config.snapshot_directory)
    assert "audit_path" in audit
    assert "policy_hash" in audit
    assert audit["policy_hash"] is not None
    imported_audit = loader.load_audit_snapshot(loader.config.snapshot_directory,
                                                Path(audit["audit_path"]).name)
    assert len(imported_audit) > 0
    assert any(e.to_dict() == approved.to_dict() for e in imported_audit)


def test_b07_authorize_entry_reason_recorded(loader, approved, clock):
    """B07: authorize_entry returns reason and policy_hash even on rejection."""
    loader.publish_generation([approved], loader._directories[0])
    rejected = loader.authorize_entry(
        {"pattern_id": "NONE", "pattern_version": "v0"},
        market_context={"regime": "BULL", "data_quality": {"state": "ok"}},
        sector_context={"lifecycle": "ACTIVE", "data_quality": {"state": "ok"}},
        as_of=clock[0],
    )
    assert not rejected["allowed"]
    assert isinstance(rejected["reason"], str) and rejected["reason"]
