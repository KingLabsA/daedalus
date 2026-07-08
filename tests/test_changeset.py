"""Tests for core.changeset — record, review, accept/reject, path safety."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.changeset import ChangesetManager, safe_repo_path


@pytest.fixture
def cs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return ChangesetManager()


def _write(cs, path, new_content, cid="c1", tool="write_file", result="Wrote file"):
    cs._on_pre_tool(calls=[{"id": cid, "name": tool, "args": {"filepath": path}}])
    Path(path).write_text(new_content)  # simulate the tool applying
    cs._on_post_tool(results=[{"id": cid, "result": result}])


def test_records_edit_with_diff(cs):
    Path("a.txt").write_text("old line\n")
    cs.begin_turn()
    _write(cs, "a.txt", "new line\n")
    s = cs.summary()
    assert s["files"][0]["path"] == "a.txt"
    assert s["files"][0]["status"] == "applied"
    assert "-old line" in s["files"][0]["diff"] and "+new line" in s["files"][0]["diff"]


def test_ignores_reads_errors_and_noops(cs):
    Path("b.txt").write_text("same\n")
    cs.begin_turn()
    cs._on_pre_tool(calls=[{"id": "r1", "name": "read_file", "args": {"filepath": "b.txt"}}])
    cs._on_post_tool(results=[{"id": "r1", "result": "same"}])
    _write(cs, "b.txt", "same\n")  # no-op content
    _write(cs, "c.txt", "x", cid="c2", result="ToolError: denied")
    assert cs.summary()["files"] == []


def test_reject_restores_and_deletes_created(cs):
    Path("keep.txt").write_text("original\n")
    cs.begin_turn()
    _write(cs, "keep.txt", "mutated\n", cid="c1")
    _write(cs, "brand_new.txt", "created\n", cid="c2")
    cs_id = cs.summary()["id"]
    assert "Reverted" in cs.reject(cs_id, "keep.txt")
    assert Path("keep.txt").read_text() == "original\n"
    assert "Reverted" in cs.reject(cs_id, "brand_new.txt")
    assert not Path("brand_new.txt").exists()  # created file removed on reject
    assert "already reverted" in cs.reject(cs_id, "keep.txt")


def test_accept_marks_and_unknown_paths(cs):
    Path("d.txt").write_text("1\n")
    cs.begin_turn()
    _write(cs, "d.txt", "2\n")
    cs_id = cs.summary()["id"]
    assert "Accepted" in cs.accept(cs_id, "d.txt")
    assert cs.summary(cs_id)["files"][0]["status"] == "accepted"
    assert "No entry" in cs.accept(cs_id, "ghost.txt")
    assert "No entry" in cs.reject("cs_999", "d.txt")


def test_turn_grouping_and_cap(cs):
    for i in range(25):
        cs.begin_turn()
        _write(cs, f"f{i}.txt", f"v{i}\n", cid=f"c{i}")
    turns = cs.list_turns()
    assert len(turns) <= 20
    assert cs.summary()["files"][0]["path"] == "f24.txt"  # latest turn


def test_hooks_never_raise(cs):
    cs._on_pre_tool(calls=None)
    cs._on_post_tool(results=[{"bad": "shape"}])
    cs._on_post_tool(results=None)


def test_safe_repo_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    ok = safe_repo_path("src/app.py")
    assert ok is not None and str(ok).startswith(str(tmp_path.resolve()))
    assert safe_repo_path("../../etc/passwd") is None
    assert safe_repo_path("/etc/passwd") is None
