from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from analysis import runner
from api import product_main

# A tiny, deterministic script the stubbed planner "writes": one figure + one table.
_FIG_TABLE_CODE = (
    "import matplotlib.pyplot as plt\n"
    "import csv\n"
    "plt.figure(); plt.plot([1, 2, 3], [2, 3, 1]); plt.title('demo')\n"
    "plt.savefig('outputs/chart.png')\n"
    "with open('outputs/table.csv', 'w', newline='') as f:\n"
    "    w = csv.writer(f); w.writerow(['x', 'y']); w.writerow([1, 2])\n"
    "print('fertig')\n"
)


def _plan_response(code: str, title: str = "Demo-Analyse") -> str:
    return json.dumps({"title": title, "description": "Erzeugt eine Figur und eine Tabelle.", "code": code})


@pytest.fixture
def analysis_client(tmp_path, monkeypatch):
    """TestClient with the managed workspace redirected into tmp and the LLM stubbed."""
    ws_base = tmp_path / "ws"
    monkeypatch.setattr(product_main.workspace_manager, "base_dir", lambda *a, **k: ws_base)

    calls: dict[str, str] = {"code": _FIG_TABLE_CODE, "title": "Demo-Analyse"}

    def fake_chat(messages, provider=None, overrides=None):  # noqa: ANN001
        return _plan_response(calls["code"], calls["title"])

    monkeypatch.setattr(product_main.llm_router, "chat", fake_chat)
    client = TestClient(product_main.app)
    return client, tmp_path, calls


# --------------------------------------------------------------------------- #
# runner unit tests (no LLM, no HTTP)                                          #
# --------------------------------------------------------------------------- #


def test_runner_produces_and_hashes_artifacts(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    result = runner.run_script(run_dir, _FIG_TABLE_CODE, seed=42, timeout=60)
    assert result.ok and result.returncode == 0 and not result.timed_out
    kinds = {a.kind for a in result.artifacts}
    assert {"figure", "table", "log"} <= kinds
    assert result.combined_hash
    # Präambel + Seed lands in script.py
    assert "PYTHONHASHSEED" in (run_dir / "script.py").read_text(encoding="utf-8")


def test_runner_is_deterministic(tmp_path):
    hashes = []
    for i in range(2):
        d = tmp_path / f"r{i}"
        d.mkdir()
        hashes.append(runner.run_script(d, _FIG_TABLE_CODE, seed=7, timeout=60).combined_hash)
    assert hashes[0] == hashes[1]


def test_runner_timeout(tmp_path):
    run_dir = tmp_path / "slow"
    run_dir.mkdir()
    result = runner.run_script(run_dir, "import time\ntime.sleep(5)\n", seed=1, timeout=1)
    assert result.timed_out and not result.ok


def test_stage_input_neutralizes_traversal(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    src = tmp_path / "data.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    # A traversal name is reduced to its basename and stays inside inputs/ — the file
    # never lands outside the run folder.
    rel = runner.stage_input(run_dir, src, "../../evil.csv")
    assert rel == "inputs/evil.csv"
    assert (run_dir / "inputs" / "evil.csv").is_file()
    assert not (tmp_path / "evil.csv").exists()


# --------------------------------------------------------------------------- #
# API flow                                                                     #
# --------------------------------------------------------------------------- #


def test_analysis_run_create_serve_and_revise(analysis_client):
    client, tmp_path, calls = analysis_client
    db_path = tmp_path / "metadata.duckdb"
    common = {"metadata_db_path": str(db_path)}

    created = client.post(
        "/analysis/runs",
        json={"request": "Plotte eine Demo-Kurve und eine Tabelle", **common},
    )
    assert created.status_code == 200, created.text
    run = created.json()["run"]
    assert run["status"] == "ok"
    assert run["output_hash"]
    figures = [a for a in run["artifacts"] if a["kind"] == "figure"]
    tables = [a for a in run["artifacts"] if a["kind"] == "table"]
    assert figures and tables
    assert figures[0]["url"].startswith("/analysis/artifacts/")

    # Provenance folder exists on disk with README + run.json + git repo.
    run_dir = Path(run["run_dir"])
    assert (run_dir / "README.md").is_file()
    assert (run_dir / "run.json").is_file()
    provenance = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert provenance["seed"] == 42 and "environment" in provenance
    assert (run_dir.parent / ".git").is_dir()

    # The figure is served as a real PNG.
    fig = client.get(figures[0]["url"], params=common)
    assert fig.status_code == 200
    assert fig.content[:8] == b"\x89PNG\r\n\x1a\n"

    # Listing + detail.
    listing = client.get("/analysis/runs", params=common)
    assert any(r["id"] == run["id"] for r in listing.json()["runs"])
    detail = client.get(f"/analysis/runs/{run['id']}", params=common)
    assert detail.status_code == 200

    # Revise with a different script → new content, status ok, different hash.
    calls["code"] = (
        "import matplotlib.pyplot as plt\n"
        "plt.figure(); plt.bar([1, 2, 3], [3, 1, 2]); plt.savefig('outputs/chart.png')\n"
    )
    revised = client.post(
        f"/analysis/runs/{run['id']}/revise",
        json={"request": "Nutze ein Balkendiagramm", **common},
    )
    assert revised.status_code == 200, revised.text
    revised_run = revised.json()["run"]
    assert revised_run["status"] == "ok"
    assert revised_run["output_hash"] != run["output_hash"]


def test_analysis_artifact_not_found(analysis_client):
    client, tmp_path, _ = analysis_client
    db_path = tmp_path / "metadata.duckdb"
    resp = client.get("/analysis/artifacts/does-not-exist", params={"metadata_db_path": str(db_path)})
    assert resp.status_code == 404
