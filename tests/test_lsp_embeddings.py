"""Tests for core.intel.lsp + embeddings. Offline: scripted fake LSP server, fake embed_fn."""
import json
import re
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.intel import EmbeddingIndex, HybridSearch, LspClient, SemanticIndex

# A real Content-Length-framed LSP server: answers initialize/definition/references,
# publishes diagnostics on didOpen, and issues a server->client workspace/configuration
# request to prove the client replies (a hanging server would time the tests out).
FAKE_LSP = textwrap.dedent(r"""
    import json, sys
    def send(msg):
        body = json.dumps(msg).encode()
        sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        sys.stdout.buffer.flush()
    def read():
        headers = {}
        line = sys.stdin.buffer.readline()
        if not line: return None
        while line.strip():
            k, v = line.split(b":", 1); headers[k.strip().lower()] = v.strip()
            line = sys.stdin.buffer.readline()
        return json.loads(sys.stdin.buffer.read(int(headers[b"content-length"])))
    while True:
        msg = read()
        if msg is None: break
        m = msg.get("method")
        if m == "initialize":
            send({"jsonrpc":"2.0","id":msg["id"],"result":{"capabilities":{}}})
            send({"jsonrpc":"2.0","id":999,"method":"workspace/configuration",
                  "params":{"items":[{"section":"python"}]}})  # client must answer this
        elif m == "textDocument/didOpen":
            uri = msg["params"]["textDocument"]["uri"]
            send({"jsonrpc":"2.0","method":"textDocument/publishDiagnostics",
                  "params":{"uri":uri,"diagnostics":[
                      {"range":{"start":{"line":2,"character":0},"end":{"line":2,"character":5}},
                       "severity":1,"message":"fake type error"}]}})
        elif m == "textDocument/definition":
            send({"jsonrpc":"2.0","id":msg["id"],"result":{
                "uri":"file:///proj/target.py",
                "range":{"start":{"line":9,"character":4},"end":{"line":9,"character":10}}}})
        elif m == "textDocument/references":
            send({"jsonrpc":"2.0","id":msg["id"],"result":[
                {"uri":"file:///proj/a.py","range":{"start":{"line":0,"character":0},"end":{"line":0,"character":1}}},
                {"uri":"file:///proj/b.py","range":{"start":{"line":4,"character":2},"end":{"line":4,"character":3}}}]})
        elif "id" in msg:
            send({"jsonrpc":"2.0","id":msg["id"],"result":None})
""")


@pytest.fixture
def lsp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    server = tmp_path / "fake_lsp.py"
    server.write_text(FAKE_LSP)
    (tmp_path / "code.py").write_text("import os\n\nx = broken\n")
    client = LspClient(str(tmp_path), servers={
        ".py": ("fake-lsp", [sys.executable, str(server)], "n/a"),
    })
    # bypass shutil.which for our python-script server
    monkeypatch.setattr("core.intel.lsp.shutil.which", lambda c: c)
    yield client
    client.close_all()


def test_lsp_definition_normalized(lsp):
    result = lsp.definition("code.py", 3, 5)
    assert result == [{"file": "/proj/target.py", "line": 10, "character": 5}]


def test_lsp_references(lsp):
    result = lsp.references("code.py", 3, 5)
    assert len(result) == 2 and result[1]["file"] == "/proj/b.py" and result[1]["line"] == 5


def test_lsp_diagnostics_published(lsp):
    result = lsp.diagnostics("code.py", timeout=8)
    assert result and result[0]["message"] == "fake type error" and result[0]["line"] == 3


def test_lsp_unregistered_extension(lsp):
    assert "No language server registered" in lsp.definition("style.css", 1, 1)


def test_lsp_not_installed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x=1\n")
    client = LspClient(str(tmp_path))  # real registry
    monkeypatch.setattr("core.intel.lsp.shutil.which", lambda c: None)
    out = client.definition("a.py", 1, 1)
    assert "not installed" in out and "npm install" in out


# ── embeddings ───────────────────────────────────────────────

def _fake_embed(texts):
    # deterministic tiny "embedding": [len, vowels, 'pay' mentions]
    return [[float(len(t)), float(sum(t.count(v) for v in "aeiou")),
             float(t.lower().count("payment") * 50)] for t in texts]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "payments.py").write_text("def charge_payment(card):\n    '''payment processing'''\n    pass\n")
    (tmp_path / "zoo.py").write_text("def feed_animals():\n    pass\n")
    return tmp_path


def test_embedding_search_relevance(repo):
    idx = EmbeddingIndex(str(repo), embed_fn=_fake_embed, cache_path=str(repo / "cache.json"))
    hits = idx.search("payment processing charge")
    assert hits and hits[0]["file"] == "payments.py"


def test_embedding_cache_reused(repo):
    calls = {"n": 0}

    def counting_embed(texts):
        calls["n"] += len(texts)
        return _fake_embed(texts)

    cache = str(repo / "cache.json")
    idx1 = EmbeddingIndex(str(repo), embed_fn=counting_embed, cache_path=cache)
    idx1.build()
    first = calls["n"]
    idx2 = EmbeddingIndex(str(repo), embed_fn=counting_embed, cache_path=cache)
    report = idx2.build()
    assert report["embedded_new"] == 0          # everything served from hash cache
    assert calls["n"] == first
    # touching a file re-embeds only that file
    (repo / "zoo.py").write_text("def feed_animals():\n    return 'fed'\n")
    idx3 = EmbeddingIndex(str(repo), embed_fn=counting_embed, cache_path=cache)
    report3 = idx3.build()
    assert 0 < report3["embedded_new"] <= 2


def test_hybrid_falls_back_to_tfidf(repo):
    dead = EmbeddingIndex(str(repo), embed_fn=lambda t: None, cache_path=str(repo / "c.json"))
    hybrid = HybridSearch(dead, SemanticIndex(str(repo)))
    assert hybrid.mode() == "tfidf"
    hits = hybrid.search("payment processing charge")
    assert hits and hits[0]["file"] == "payments.py"


def test_hybrid_prefers_embeddings(repo):
    live = EmbeddingIndex(str(repo), embed_fn=_fake_embed, cache_path=str(repo / "c2.json"))
    hybrid = HybridSearch(live, SemanticIndex(str(repo)))
    assert hybrid.mode() == "embeddings"
    assert hybrid.search("payment")[0]["file"] == "payments.py"


def test_no_agent_ultimate_dependency():
    for mod in ("lsp", "embeddings"):
        src = (Path(__file__).parent.parent / "core" / "intel" / f"{mod}.py").read_text()
        assert not re.search(r"^\s*(?:from|import)\s+agent_ultimate", src, re.M), mod
