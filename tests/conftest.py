"""Test-suite compatibility markers for the v0.7.5 provenance migration.

The legacy v0.7.4 corporate-action reconciliation test asserted exact counts
(91 MATCHED / 7 MISSING) produced by a self-derived "official" register. v0.7.5
intentionally removes that behavior and fails closed unless an independently
sourced, hashed official register is mounted. The obsolete assertion remains in
the historical test file for traceability, but is strict-xfailed here; the new
v0.7.5 provenance tests verify the replacement behavior directly.
"""
from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        if (
            item.name == "test_corporate_action_set_reconciliation_audit"
            and item.path.name == "test_data_layer_pit.py"
        ):
            item.add_marker(
                pytest.mark.xfail(
                    reason=(
                        "v0.7.4 exact CA counts came from a self-derived official register; "
                        "superseded by v0.7.5 independent provenance/fail-closed tests"
                    ),
                    strict=True,
                )
            )
