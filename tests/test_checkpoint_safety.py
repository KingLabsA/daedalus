"""Regression: creating a checkpoint must NOT wipe uncommitted working-tree changes.

Previously CheckpointManager used `git stash push`, which removed changes from the
working tree — silently discarding a user's (or a concurrent process's) edits.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    def run(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    run("init", "-q")
    run("config", "user.email", "t@t.t")
    run("config", "user.name", "t")
    (tmp_path / "tracked.txt").write_text("committed content\n")
    run("add", "-A")
    run("commit", "-qm", "initial")
    monkeypatch.chdir(tmp_path)
    # point checkpoint storage inside the repo
    import agent_ultimate as au
    monkeypatch.setattr(au, "CHECKPOINTS_DIR", tmp_path / ".hermes" / "checkpoints")
    au.CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_checkpoint_preserves_working_tree(git_repo):
    from agent_ultimate import CheckpointManager
    # make an uncommitted edit
    (git_repo / "tracked.txt").write_text("UNCOMMITTED EDIT\n")
    out = CheckpointManager.create_checkpoint("safe-test")
    assert "Checkpoint created" in out
    # the edit MUST still be in the working tree
    assert (git_repo / "tracked.txt").read_text() == "UNCOMMITTED EDIT\n"
    # and it was recorded with a recoverable sha
    meta = json.loads((git_repo / ".hermes" / "checkpoints" / "safe-test" / "meta.json").read_text())
    assert meta["sha"]


def test_checkpoint_restore_applies_snapshot(git_repo):
    from agent_ultimate import CheckpointManager
    # snapshot some experimental work
    (git_repo / "tracked.txt").write_text("EXPERIMENTAL WORK\n")
    CheckpointManager.create_checkpoint("cpA")
    # discard it back to the committed state
    subprocess.run(["git", "checkout", "--", "tracked.txt"], cwd=git_repo, check=True, capture_output=True)
    assert (git_repo / "tracked.txt").read_text() == "committed content\n"
    # restoring the checkpoint brings the experimental work back
    out = CheckpointManager.restore_checkpoint("cpA")
    assert "Restored checkpoint" in out
    assert "EXPERIMENTAL WORK" in (git_repo / "tracked.txt").read_text()


def test_checkpoint_clean_tree_is_noop(git_repo):
    from agent_ultimate import CheckpointManager
    out = CheckpointManager.create_checkpoint("nothing")
    assert "Nothing to checkpoint" in out
