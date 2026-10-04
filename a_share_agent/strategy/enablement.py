"""Fail-closed, physically bound policy records. No execution permissions live here."""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, fields, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

EvidenceKey = tuple[str, str, str, str]
# Producer rule-section names and their exact serialized SHA-256 fields.
RULE_HASH_SCHEMA = {name: f"{name}_sha256" for name in
                    ("router", "scanner", "regime", "context", "scoring", "cost_source")}

def validate_rule_hashes(hashes):
    if not isinstance(hashes, dict) or set(hashes) != set(RULE_HASH_SCHEMA.values()):
        raise ValueError("Missing or unsupported rule hash schema")
    for name, value in hashes.items():
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError(f"Malformed rule hash: {name}")
        if value == hashlib.sha256(b"{}").hexdigest():
            raise ValueError(f"Empty source rule hash: {name}")
    return hashes


def _key(value) -> EvidenceKey:
    if not isinstance(value, (tuple, list)) or len(value) != 4 or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError("Expected exact four-key string identity")
    if any(v in {"PANIC", "UNKNOWN", "DEGRADED"} for v in value):
        raise ValueError("PANIC/UNKNOWN/DEGRADED cannot authorize")
    return tuple(value)


def _timestamp(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite numeric timestamp")
    return float(value)


def _iso_timestamp(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("generated_at requires timezone")
    return dt.timestamp()


def _safe_path(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symlink rejected")
    return path


def _compute_file_hash(path):
    return hashlib.sha256(_safe_path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class PolicyConfig:
    expiry_days: int = 90
    allow_future_effective: bool = False
    max_file_age_seconds: int = 86400
    directories: tuple[str, ...] = ("data/policy/live",)
    snapshot_directory: str = "data/policy/audit"
    enable_snapshots: bool = True

    def __post_init__(self):
        for name in ("expiry_days", "max_file_age_seconds"):
            value = getattr(self, name)
            if type(value) is not int or value < 0 or (name == "expiry_days" and value == 0):
                raise ValueError(f"invalid {name}")
        if type(self.allow_future_effective) is not bool or type(self.enable_snapshots) is not bool:
            raise ValueError("configuration booleans must be booleans")
        if not isinstance(self.directories, (list, tuple)) or any(not isinstance(v, str) or not v for v in self.directories):
            raise ValueError("invalid directories")
        object.__setattr__(self, "directories", tuple(self.directories))
        if not isinstance(self.snapshot_directory, str) or not self.snapshot_directory:
            raise ValueError("invalid snapshot_directory")


def load_config_from_yaml(path=None):
    if path is None or not Path(path).exists():
        return PolicyConfig()
    import yaml
    try:
        data = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Failed to load policy config: {exc}") from exc
    allowed = {"loading": {"directories", "max_file_age_seconds"}, "defaults": {"expiry_days"}, "validation": {"allow_future_effective"}, "audit": {"snapshot_directory", "enable_snapshots"}}
    if not isinstance(data, dict) or set(data) - set(allowed) - {"version"}:
        raise ValueError("Unknown configuration sections")
    kwargs = {}
    for section, names in allowed.items():
        values = data.get(section, {})
        if not isinstance(values, dict) or set(values) - names:
            raise ValueError(f"Unsupported configuration knobs: {section}")
        kwargs.update(values)
    return PolicyConfig(**kwargs)


@dataclass(frozen=True, init=False, slots=True)
class PhysicalEvidence:
    key: EvidenceKey
    producer_sha: str
    artifact_hash: str
    manifest_hash: str
    manifest_path: str
    generation_id: str
    classification: str
    context_version: str
    config_hash: str
    pattern_config_hash: str
    context_config_hash: str
    generated_at: str
    run_id: str
    wf_config_hash: str
    verification_result: str
    evidence_type: str

    def __init__(self, *args, **kwargs):
        raise TypeError("PhysicalEvidence is factory-only; use verify_physical_artifact")


def _record(cls, data):
    obj = object.__new__(cls)
    for field in fields(cls):
        object.__setattr__(obj, field.name, data[field.name])
    return obj


def verify_physical_artifact(run_dir, evidence_key, config=None):
    """Verify published producer files, then recompute eligibility from committed reports."""
    from ..backtest.oos_stability import verify_per_key_oos_artifact, per_key_oos_stability, _key_to_artifact_key
    key = _key(evidence_key)
    directory = _safe_path(run_dir)
    manifest_path = _safe_path(directory / "manifest.json")
    manifest = json.loads(manifest_path.read_text())
    relative = manifest.get("artifact_path")
    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Invalid physical artifact path")
    artifact_path = _safe_path(directory / relative)
    artifact = json.loads(artifact_path.read_text())
    hashes = validate_rule_hashes(artifact.get("rule_hashes"))
    validate_rule_hashes(manifest.get("rule_hashes"))
    if config is not None and hashes != config.rule_hashes:
        raise ValueError("Current configuration rule hashes do not match physical evidence")
    if config is not None and artifact.get("config_hash") != config.config_hash:
        raise ValueError("Current configuration hash does not match physical evidence")
    verification = verify_per_key_oos_artifact(artifact, request_key=key, manifest_path=str(manifest_path))
    if not verification["valid"] or not verification["key_eligible"]:
        raise ValueError(f"Physical per-key evidence non-authorizing: {verification}")
    artifact_generated = _iso_timestamp(artifact["generated_at"])
    manifest_generated = _iso_timestamp(manifest["generated_at"])
    generated = max(artifact_generated, manifest_generated)
    if generated > time.time():
        raise ValueError("future evidence/publication rejected")
    plan = manifest.get("fold_plan", {}).get("folds")
    if not isinstance(plan, list) or not plan:
        raise ValueError("Missing committed fold plan")
    reports = [json.loads(_safe_path(directory / name).read_text()) for name in sorted(manifest["output_files"]) if Path(name).name == "report.json"]
    if not reports:
        raise ValueError("Missing committed original fold reports")
    settings = manifest.get("settings", {})
    recomputed = per_key_oos_stability(reports, thresholds=settings.get("stability_thresholds"), declared_keys=[_key(k) for k in manifest.get("declared_keys") or [key]], expected_fold_ids=[f["fold_id"] for f in plan])
    encoded = _key_to_artifact_key(key)
    actual = artifact.get("keys", {}).get(encoded)
    expected = recomputed["keys"].get(encoded)
    if actual != expected or not expected or expected["classification"] != "STABLE_CANDIDATE":
        raise ValueError("Candidate classification/identity does not match physical report recomputation")
    manifest_hash = _compute_file_hash(manifest_path)
    # Recheck completion after reads: an artifact mutation cannot change the binding silently.
    if json.loads((directory / "completion.json").read_text())["manifest_sha256"] != manifest_hash:
        raise ValueError("publication changed during verification")
    generated_iso = artifact["generated_at"] if artifact_generated >= manifest_generated else manifest["generated_at"]
    return _record(PhysicalEvidence, dict(key=key, producer_sha=artifact["source_sha"], artifact_hash=_compute_file_hash(artifact_path), manifest_hash=manifest_hash, manifest_path=str(manifest_path), generation_id=manifest["generation_id"], classification=expected["classification"], context_version=hashes["context_sha256"], config_hash=artifact["config_hash"], pattern_config_hash=hashes["scanner_sha256"], context_config_hash=hashes["context_sha256"], generated_at=generated_iso, run_id=artifact["run_id"], wf_config_hash=artifact["wf_config_hash"], verification_result="VERIFIED", evidence_type="per_key"))


class PolicyStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    EXPIRED = "expired"
    REVOKED = "revoked"
    DISABLED = "disabled"


@dataclass(frozen=True, init=False, slots=True)
class PolicyEntry:
    key: EvidenceKey
    evidence_id: str
    evidence_version: str
    context_version: str
    config_hash: str
    pattern_config_hash: str
    context_config_hash: str
    source_producer_sha: str
    source_run_id: str
    manifest_path: str
    manifest_hash: str
    source_generation_id: str
    generated_at_ts: float
    issued_at: float
    valid_from: float
    expires_at: float
    approved_at: float | None
    approval_authority: str | None
    status: PolicyStatus
    reason: str | None
    schema_version: int

    def __init__(self, *args, **kwargs):
        raise TypeError("PolicyEntry is factory-only; use from_evidence/from_snapshot")

    @classmethod
    def from_evidence(cls, evidence_key, evidence, valid_days=None, valid_from=None, approval_authority=None, config=None):
        if type(evidence) is not PhysicalEvidence:
            raise TypeError("evidence must be verified PhysicalEvidence")
        key = _key(evidence_key)
        if key != evidence.key:
            raise ValueError("exact key mismatch")
        fresh = verify_physical_artifact(Path(evidence.manifest_path).parent, key)
        if fresh != evidence:
            raise ValueError("physical evidence binding changed")
        config = config or PolicyConfig()
        now = time.time()
        vf = now if valid_from is None else _timestamp(valid_from, "valid_from")
        if vf < now or (vf > now and not config.allow_future_effective):
            raise ValueError("invalid policy effective time")
        days = config.expiry_days if valid_days is None else valid_days
        if type(days) is not int or days <= 0:
            raise ValueError("valid_days must be positive")
        data = dict(key=key, evidence_id=evidence.artifact_hash, evidence_version=evidence.manifest_hash, context_version=evidence.context_version, config_hash=evidence.config_hash, pattern_config_hash=evidence.pattern_config_hash, context_config_hash=evidence.context_config_hash, source_producer_sha=evidence.producer_sha, source_run_id=evidence.run_id, manifest_path=evidence.manifest_path, manifest_hash=evidence.manifest_hash, source_generation_id=evidence.generation_id, generated_at_ts=_iso_timestamp(evidence.generated_at), issued_at=now, valid_from=vf, expires_at=vf+days*86400, approved_at=None, approval_authority=None, status=PolicyStatus.PENDING, reason="Awaiting explicit approval", schema_version=1)
        entry = cls.from_dict(data)
        return entry.approve(approval_authority) if approval_authority is not None else entry

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) != {f.name for f in fields(cls)}:
            raise ValueError("Invalid policy snapshot schema")
        d = dict(data)
        d["key"] = _key(d["key"])
        if type(d["schema_version"]) is not int or d["schema_version"] != 1:
            raise ValueError("Unsupported policy schema")
        try:
            d["status"] = PolicyStatus(d["status"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid status") from exc
        for name in ("generated_at_ts", "issued_at", "valid_from", "expires_at"):
            d[name] = _timestamp(d[name], name)
        if not d["generated_at_ts"] <= d["issued_at"] <= d["valid_from"] < d["expires_at"]:
            raise ValueError("Policy times not ordered")
        if d["approved_at"] is not None:
            d["approved_at"] = _timestamp(d["approved_at"], "approved_at")
            if not d["issued_at"] <= d["approved_at"] < d["expires_at"]:
                raise ValueError("Approval time outside policy lifetime")
        authority = d["approval_authority"]
        if authority is not None and (not isinstance(authority, str) or not authority.strip()):
            raise ValueError("approval_authority must be non-empty")
        if (authority is None) != (d["approved_at"] is None):
            raise ValueError("Incomplete approval metadata")
        if d["status"] == PolicyStatus.APPROVED and authority is None:
            raise ValueError("APPROVED requires explicit approval")
        if d["status"] == PolicyStatus.PENDING and authority is not None:
            raise ValueError("PENDING cannot carry approval")
        if d["reason"] is not None and not isinstance(d["reason"], str):
            raise ValueError("invalid reason")
        evidence = verify_physical_artifact(Path(d["manifest_path"]).parent, d["key"])
        bindings = {"evidence_id": evidence.artifact_hash, "evidence_version": evidence.manifest_hash, "manifest_hash": evidence.manifest_hash, "manifest_path": evidence.manifest_path, "source_generation_id": evidence.generation_id, "source_run_id": evidence.run_id, "source_producer_sha": evidence.producer_sha, "generated_at_ts": _iso_timestamp(evidence.generated_at), "context_version": evidence.context_version, "config_hash": evidence.config_hash, "pattern_config_hash": evidence.pattern_config_hash, "context_config_hash": evidence.context_config_hash}
        if any(d[k] != v for k, v in bindings.items()):
            raise ValueError("Snapshot physical artifact binding mismatch")
        return _record(cls, d)

    def is_valid_at(self, as_of):
        as_of = _timestamp(as_of, "as_of")
        return self.status == PolicyStatus.APPROVED and self.key[0] != "PANIC" and self.approved_at is not None and max(self.generated_at_ts, self.issued_at, self.valid_from, self.approved_at) <= as_of < self.expires_at

    @property
    def is_valid(self):
        return self.is_valid_at(time.time())

    @property
    def is_enabled(self):
        return self.is_valid

    @property
    def is_expired(self):
        return time.time() >= self.expires_at

    @property
    def failure_reason(self):
        return None if self.is_valid else self.reason or f"policy {self.status.value} or outside validity window"

    def _transition(self, status, reason, **changes):
        data = self.to_dict()
        data.update(status=status, reason=reason, **changes)
        return self.from_dict(data)

    def approve(self, approver, reason=None):
        if self.status != PolicyStatus.PENDING:
            raise ValueError("Only PENDING may be approved")
        return self._transition(PolicyStatus.APPROVED, reason, approved_at=time.time(), approval_authority=approver)

    def disable(self, reason):
        return self._transition(PolicyStatus.DISABLED, reason)

    def revoke(self, reason):
        if self.status != PolicyStatus.APPROVED:
            raise ValueError("Only APPROVED may be revoked")
        return self._transition(PolicyStatus.REVOKED, reason)

    def expire(self, reason=None):
        return self._transition(PolicyStatus.EXPIRED, reason)

    def to_dict(self):
        data = asdict(self)
        data["key"] = list(self.key)
        data["status"] = self.status.value
        return data

    def to_snapshot(self, path):
        path = _safe_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), sort_keys=True, allow_nan=False))

    @classmethod
    def from_snapshot(cls, path):
        return cls.from_dict(json.loads(_safe_path(path).read_text()))


@dataclass(frozen=True)
class EvidenceValidationResult:
    valid: bool
    status: str


def validate_evidence(artifact: dict[str, Any]):
    """Descriptive labels alone are never authorization evidence."""
    return EvidenceValidationResult(False, str(artifact.get("classification", "MISSING")))
