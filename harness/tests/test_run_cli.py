"""Offline CLI tests for `harness/run.py` (--verbose, --output-json, --mock --dry-run)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from harness import run as run_cli
from harness.models import Seed


def test_run_cli_verbose_and_output_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed = Seed(
        seed_slug="acme",
        seed_name="Acme",
        seed_domain="acme.com",
        description="Cloud billing platform",
        category="b2b-saas",
    )
    out_json = tmp_path / "summary.json"

    monkeypatch.setattr(run_cli, "read_snapshot", lambda: {"dataset_slug": "test-ds", "k": 5, "seeds": [], "seed_vendors": [], "leaderboard": []})
    monkeypatch.setattr(run_cli, "read_seeds", lambda: [seed])
    monkeypatch.setattr(run_cli, "THROTTLE_SEC", 0.0)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "harness.run",
            "--mock",
            "--dry-run",
            "--only",
            "parallel",
            "--k",
            "5",
            "--verbose",
            "--output-json",
            str(out_json),
        ],
    )

    rc = run_cli.main()
    assert rc == 0

    stdout = capsys.readouterr().out
    assert "cfg=" in stdout
    assert "status=ok" in stdout
    assert out_json.exists()

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["k"] == 5
    assert payload["mock"] is True
    assert payload["dry_run"] is True
    assert payload["cells_judged"] == 1
    assert payload["cells_failed"] == 0
    assert len(payload["cells"]) == 1
    assert payload["cells"][0]["seed"] == "acme"
    assert payload["cells"][0]["vendor"] == "parallel"
    assert payload["cells"][0]["precision_at_k"] is not None
