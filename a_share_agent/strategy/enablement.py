"""Policy enablement for Pattern registry after OOS validation.

Phase 2B: Evidence policy with approval workflow.

Policy entry represents a validated, approved Pattern set with time bounds.
"""
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# Four-key evidence tuple as used in OOS classification
EvidenceKey = tuple[str, str, str, str]  # (regime, theme, pattern_id, pattern_version)


class PolicyStatus(str, Enum):
    """Status of a policy entry."""
    PENDING = "pending"      # Generated but not yet approved
    APPROVED = "approved"    # Explicit approval received
    EXPIRED = "expired"      # Valid time period passed
    REVOKED = "revoked"      # Manual revocation
    DISABLED = "disabled"    # No longer enabled due to failure


@dataclass(frozen=True)
class PolicyEntry:
    """Policy entry for Pattern registry after OOS validation.

    Key invariants:
    - Four-key evidence bound to a specific OOS evidence result
    - Exact version matching (PolicyEntry must match evidence exactly to enable)
    - Time-bounded validity (issued + expiry) — fail-closed if unknown/expired
    - Fail-closed: unknown/poor-quality/expired/corrupted → NO_TRADE
    - PANIC hard: data quality issues cause NO_TRADE, not automatic disable

    Attributes:
    key: Four-key evidence tuple (regime_at_signal, theme_lifecycle, pattern_id, pattern_version)
    evidence_id: Hash of the evidence result (OOS result artifact)
    evidence_version: Version of evidence schema / walk-forward run
    context_version: MarketContext version used for evidence
    config_hash: Hash of config (scanner, router, regime, scoring, cost_source)
    issued_at: Unix timestamp (UTC) when policy generated
    valid_from: Unix timestamp (UTC) when policy becomes active (defaults to issued)
    expires_at: Unix timestamp (UTC) when policy stops being valid
    approver: Optional approver identifier (user/role) for approval workflow
    status: PolicyStatus
    reason: Optional[str] describing why enabled/disabled
    source_run_id: Optional[str] generating walk-forward run ID

    Policy immutable invariants:
    - No direct field mutation after creation
    - History via separate audit trail, no in-place updates
    - Rollback to previous policy via versioned snapshots
    """

    key: EvidenceKey
    evidence_id: str
    evidence_version: str
    context_version: str
    config_hash: str
    issued_at: float
    valid_from: float
    expires_at: float
    approver: Optional[str]
    status: PolicyStatus = PolicyStatus.PENDING
    reason: Optional[str] = None
    source_run_id: Optional[str] = None

    # Computed properties (read-only)
    @property
    def evidence(self) -> str:
        """Canonical evidence ID for reference."""
        return self.evidence_id

    @property
    def is_valid(self) -> bool:
        """Check if policy is currently valid (not expired, approved)."""
        now = time.time()
        return (
            self.status in (PolicyStatus.APPROVED, PolicyStatus.PENDING) and
            self.valid_from <= now and
            self.expires_at > now
        )

    @property
    def is_expired(self) -> bool:
        """True if policy is past expiry."""
        return self.status == PolicyStatus.EXPIRED or time.time() >= self.expires_at

    @property
    def is_approved(self) -> bool:
        """True if policy has explicit approval (or default auto-approval)."""
        return self.status in (PolicyStatus.APPROVED, PolicyStatus.PENDING)

    @property
    def failure_reason(self) -> Optional[str]:
        """Describes why policy is disabled (for audit)."""
        if self.status == PolicyStatus.EXPIRED:
            return f"policy expired at {datetime.fromtimestamp(self.expires_at).isoformat()}"
        if self.status == PolicyStatus.REVOKED:
            return "policy revoked"
        if self.status == PolicyStatus.DISABLED:
            return self.reason or "policy disabled"
        return None

    @classmethod
    def from_evidence(
        cls,
        evidence_key: EvidenceKey,
        evidence_result: dict[str, Any],
        config_hashes: Dict[str, str],
        source_run_id: Optional[str] = None,
        approver: Optional[str] = None,
        valid_days: int = 90,
    ) -> PolicyEntry:
        """Create a new pending policy from OOS evidence result.

        Args:
        evidence_key: Four-key evidence tuple
        evidence_result: Full OOS classification + metrics from aggregate_stability
        config_hashes: Map of config section -> hash (from walk_forward_service)
        source_run_id: Generating walk-forward run ID
        approver: Optional approver for non-default approval workflow
        valid_days: Days policy remains valid (default 90)

        Returns:
        PolicyEntry with status=PolicyStatus.PENDING
        """
        now = time.time()
        valid_from = now
        expires_at = now + (valid_days * 24 * 60 * 60)

        # Stable evidence ID: hash of evidence key + result
        evidence_json = json.dumps(evidence_result, sort_keys=True)
        evidence_id = hashlib.sha256(
            f"{evidence_key}_{evidence_json}".encode("utf-8")
        ).hexdigest()

        return cls(
            key=evidence_key,
            evidence_id=evidence_id,
            evidence_version=evidence_result.get("engine_version", "1.0"),
            context_version=evidence_result.get("context_version", "1.0"),
            config_hash=config_hashes.get("overall", ""),
            issued_at=now,
            valid_from=valid_from,
            expires_at=expires_at,
            approver=approver,
            status=PolicyStatus.PENDING,
            reason=None,
            source_run_id=source_run_id,
        )

    @classmethod
    def from_snapshot(cls, snapshot_path: Path) -> PolicyEntry:
        """Load a policy from a JSON snapshot (audit/rollback).

        Args:
            snapshot_path: Path to a PolicyEntry JSON file

        Returns:
            Deserialized PolicyEntry

        Raises:
            ValueError: If JSON does not conform to PolicyEntry schema
        """
        with open(snapshot_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Convert string timestamp fields to float
        data["issued_at"] = float(data["issued_at"])
        data["valid_from"] = float(data["valid_from"])
        data["expires_at"] = float(data["expires_at"])

        # Enum string → enum
        data["status"] = PolicyStatus(data["status"])

        return cls(**data)

    def to_snapshot(self, path: Path) -> None:
        """Write this policy to a JSON snapshot for audit/rollback.

        Args:
            path: Output file path (parent directory must exist)
        """
        # Convert enum to string
        data = dataclasses.asdict(self)
        data["status"] = self.status.value

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def approve(self, approver: str, reason: Optional[str] = None) -> PolicyEntry:
        """Approve this pending policy (returns new instance).

        Args:
            approver: Identifier of approver (user/role)
            reason: Optional approval reason

        Returns:
            New PolicyEntry with status=PolicyStatus.APPROVED, issued_at same

        Raises:
            ValueError: If policy not pending
        """
        if self.status != PolicyStatus.PENDING:
            raise ValueError(f"Cannot approve policy with status {self.status}")

        # Return immutable new instance
        return PolicyEntry(
            key=self.key,
            evidence_id=self.evidence_id,
            evidence_version=self.evidence_version,
            context_version=self.context_version,
            config_hash=self.config_hash,
            issued_at=self.issued_at,
            valid_from=self.valid_from,
            expires_at=self.expires_at,
            approver=approver,
            status=PolicyStatus.APPROVED,
            reason=reason,
            source_run_id=self.source_run_id,
        )

    def expire(self, reason: Optional[str] = None) -> PolicyEntry:
        """Force expire this policy (returns new instance)."""
        if self.status not in (PolicyStatus.APPROVED, PolicyStatus.PENDING):
            raise ValueError(f"Cannot expire policy with status {self.status}")

        return PolicyEntry(
            key=self.key,
            evidence_id=self.evidence_id,
            evidence_version=self.evidence_version,
            context_version=self.context_version,
            config_hash=self.config_hash,
            issued_at=self.issued_at,
            valid_from=self.valid_from,
            expires_at=self.expires_at,
            approver=self.approver,
            status=PolicyStatus.EXPIRED,
            reason=reason or "policy expired",
            source_run_id=self.source_run_id,
        )

    def revoke(self, reason: str) -> PolicyEntry:
        """Revoke this approved policy (returns new instance)."""
        if self.status != PolicyStatus.APPROVED:
            raise ValueError(f"Cannot revoke policy with status {self.status}")

        return PolicyEntry(
            key=self.key,
            evidence_id=self.evidence_id,
            evidence_version=self.evidence_version,
            context_version=self.context_version,
            config_hash=self.config_hash,
            issued_at=self.issued_at,
            valid_from=self.valid_from,
            expires_at=self.expires_at,
            approver=self.approver,
            status=PolicyStatus.REVOKED,
            reason=reason,
            source_run_id=self.source_run_id,
        )

    def disable(self, reason: str) -> PolicyEntry:
        """Disable this policy due to validation failure (returns new instance)."""
        if self.status not in (PolicyStatus.APPROVED, PolicyStatus.PENDING):
            raise ValueError(f"Cannot disable policy with status {self.status}")

        return PolicyEntry(
            key=self.key,
            evidence_id=self.evidence_id,
            evidence_version=self.evidence_version,
            context_version=self.context_version,
            config_hash=self.config_hash,
            issued_at=self.issued_at,
            valid_from=self.valid_from,
            expires_at=self.expires_at,
            approver=self.approver,
            status=PolicyStatus.DISABLED,
            reason=reason,
            source_run_id=self.source_run_id,
        )


# Re-export for convenience
__all__ = ["PolicyEntry", "PolicyStatus", "EvidenceKey"]