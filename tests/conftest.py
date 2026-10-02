from pathlib import Path
import shutil
import pytest


@pytest.fixture
def runtime_root(tmp_path: Path) -> Path:
    src = Path(__file__).resolve().parents[1]
    shutil.copytree(src / "config", tmp_path / "config")
    shutil.copytree(src / "skill", tmp_path / "skill")
    return tmp_path


def pytest_collection_modifyitems(items):
    """Strict-xfail one obsolete v0.7.4 assertion that encoded self-derived CA counts.

    v0.7.5 removes the self-derived "official" corporate-action register and
    fails closed until an independently sourced, hashed register is mounted.
    The replacement behavior is covered by tests/test_data_provenance_v075.py.
    strict=True ensures this marker itself becomes a failure if the obsolete
    assertion unexpectedly starts passing again.
    """
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
