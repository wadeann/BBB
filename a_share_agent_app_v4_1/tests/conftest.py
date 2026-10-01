from pathlib import Path
import shutil
import pytest


@pytest.fixture
def runtime_root(tmp_path: Path) -> Path:
    src = Path(__file__).resolve().parents[1]
    shutil.copytree(src / "config", tmp_path / "config")
    shutil.copytree(src / "skill", tmp_path / "skill")
    return tmp_path
