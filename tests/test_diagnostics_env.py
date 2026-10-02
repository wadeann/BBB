from __future__ import annotations

import json
import os
from pathlib import Path

from a_share_agent.config import load_project_env
from a_share_agent.diagnostics import write_diagnostic_report


def test_project_env_loads_without_overriding_existing(tmp_path: Path, monkeypatch):
    (tmp_path / ".env").write_text(
        "ASHARE_TEST_A=from_file\nASHARE_TEST_B=$literal$dollar$value\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ASHARE_TEST_A", "from_process")
    monkeypatch.delenv("ASHARE_TEST_B", raising=False)
    load_project_env(tmp_path)
    assert os.environ["ASHARE_TEST_A"] == "from_process"
    assert os.environ["ASHARE_TEST_B"] == "$literal$dollar$value"


def test_diagnostic_report_redacts_and_updates_latest(tmp_path: Path):
    paths = write_diagnostic_report(
        tmp_path,
        "preflight",
        {"api_key": "secret-value", "nested": {"password": "pw"}, "result": "ok"},
        ok=True,
    )
    report = json.loads(Path(paths["report"]).read_text(encoding="utf-8"))
    latest = json.loads(Path(paths["latest"]).read_text(encoding="utf-8"))
    assert report == latest
    assert report["ok"] is True
    assert report["payload"]["api_key"] == "<REDACTED>"
    assert report["payload"]["nested"]["password"] == "<REDACTED>"
    assert report["payload"]["result"] == "ok"
