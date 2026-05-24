from pathlib import Path

from src import paths


def test_runtime_base_uses_repo_sidecar_for_frozen_dist(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    dist = repo / "dist"
    dist.mkdir(parents=True)
    (repo / ".env").write_text("PLACEHOLDER=1\n", encoding="utf-8")
    exe = dist / "IndusDispatchConsole.exe"
    exe.write_text("", encoding="utf-8")

    monkeypatch.delenv("INDUS_AGENT_HOME", raising=False)
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(exe))

    assert paths.runtime_base() == repo.resolve()


def test_runtime_base_honors_indus_agent_home(monkeypatch, tmp_path):
    home = tmp_path / "runtime"
    monkeypatch.setenv("INDUS_AGENT_HOME", str(home))

    assert paths.runtime_base() == home.resolve()
