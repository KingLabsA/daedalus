"""Tests for core.changeset — record, review, accept/reject, path safety."""

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


SRC = "line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\n"
DST = "line1\nCHANGED2\nline3\nline4\nline5\nline6\nCHANGED7\nline8\n"  # two separated edits


def test_two_separated_edits_two_hunks(cs):
    Path("h.txt").write_text(SRC)
    cs.begin_turn()
    _write(cs, "h.txt", DST)
    f = cs.summary()["files"][0]
    assert len(f["hunks"]) == 2
    assert "-line2" in f["hunks"][0]["diff"] and "+CHANGED2" in f["hunks"][0]["diff"]
    assert "-line7" in f["hunks"][1]["diff"] and "+CHANGED7" in f["hunks"][1]["diff"]


def test_reject_one_hunk_keeps_other(cs):
    Path("h.txt").write_text(SRC)
    cs.begin_turn()
    _write(cs, "h.txt", DST)
    cs_id = cs.summary()["id"]
    assert "Reverted hunk 0" in cs.reject_hunk(cs_id, "h.txt", 0)
    content = Path("h.txt").read_text()
    assert "line2" in content and "CHANGED2" not in content  # hunk 0 restored
    assert "CHANGED7" in content and "line7\n" not in content  # hunk 1 kept
    f = cs.summary(cs_id)["files"][0]
    assert f["status"] == "partial"
    assert f["hunks"][0]["status"] == "reverted" and f["hunks"][1]["status"] == "applied"


def test_reject_all_hunks_equals_full_revert(cs):
    Path("h.txt").write_text(SRC)
    cs.begin_turn()
    _write(cs, "h.txt", DST)
    cs_id = cs.summary()["id"]
    cs.reject_hunk(cs_id, "h.txt", 0)
    cs.reject_hunk(cs_id, "h.txt", 1)
    assert Path("h.txt").read_text() == SRC
    assert cs.summary(cs_id)["files"][0]["status"] == "reverted"


def test_accept_hunk_and_bounds(cs):
    Path("h.txt").write_text(SRC)
    cs.begin_turn()
    _write(cs, "h.txt", DST)
    cs_id = cs.summary()["id"]
    assert "Accepted hunk 1" in cs.accept_hunk(cs_id, "h.txt", 1)
    assert cs.summary(cs_id)["files"][0]["hunks"][1]["status"] == "accepted"
    assert "No hunk 9" in cs.accept_hunk(cs_id, "h.txt", 9)
    assert "already reverted" in (cs.reject_hunk(cs_id, "h.txt", 1) and cs.reject_hunk(cs_id, "h.txt", 1))


def test_created_file_reject_all_hunks_deletes(cs):
    cs.begin_turn()
    _write(cs, "fresh.txt", "brand new\n")
    cs_id = cs.summary()["id"]
    assert len(cs.summary(cs_id)["files"][0]["hunks"]) == 1
    cs.reject_hunk(cs_id, "fresh.txt", 0)
    assert not Path("fresh.txt").exists()


def test_chained_edits_same_turn_diff_against_original(cs):
    Path("c.txt").write_text("v0\n")
    cs.begin_turn()
    _write(cs, "c.txt", "v1\n", cid="c1")
    _write(cs, "c.txt", "v2\n", cid="c2")
    cs_id = cs.summary()["id"]
    cs.reject(cs_id, "c.txt")
    assert Path("c.txt").read_text() == "v0\n"  # revert goes to ORIGINAL, not v1


def test_original_returns_pre_edit_content(cs):
    Path("o.txt").write_text("before\n")
    cs.begin_turn()
    _write(cs, "o.txt", "after\n")
    cs_id = cs.summary()["id"]
    assert cs.original(cs_id, "o.txt") == "before\n"
    assert cs.original(cs_id, "ghost.txt") is None
    assert cs.original("cs_999", "o.txt") is None


def test_safe_repo_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    ok = safe_repo_path("src/app.py")
    assert ok is not None and str(ok).startswith(str(tmp_path.resolve()))
    assert safe_repo_path("../../etc/passwd") is None
    assert safe_repo_path("/etc/passwd") is None
