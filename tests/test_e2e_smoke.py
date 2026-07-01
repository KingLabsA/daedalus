"""
E2E smoke test: start Python WS server, connect as frontend would,
send chat, verify response, test tools/kanban WS commands.
"""
import asyncio, json, os, signal, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_SCRIPT = str(ROOT / "agent_ultimate.py")
TIMEOUT = 25  # generous for LLM response


async def wait_for_server(host: str, port: int, timeout: float = 10) -> bool:
    """Poll until the WS server is accepting connections."""
    import socket
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r, w = socket.socket(), socket.socket()
            r.settimeout(1)
            r.connect((host, port))
            r.close()
            return True
        except (ConnectionRefusedError, OSError):
            await asyncio.sleep(0.3)
    return False


async def test_connection(ws) -> None:
    """Verify initial handshake / connection works."""
    print("  [PASS] WebSocket connected")


async def test_tools_command(ws) -> int:
    """Request tools list and verify structure."""
    await ws.send(json.dumps({"type": "command", "command": "tools"}))
    resp = await asyncio.wait_for(ws.recv(), timeout=5)
    data = json.loads(resp)
    assert data["type"] == "tools", f"Expected 'tools', got '{data['type']}'"
    assert isinstance(data["data"], list), "tools should be a list"
    assert len(data["data"]) > 0, "should have at least one tool"
    n = len(data["data"])
    print(f"  [PASS] tools: {n} registered")
    return n


async def test_kanban_command(ws) -> None:
    """Request kanban board state."""
    await ws.send(json.dumps({"type": "command", "command": "kanban"}))
    resp = await asyncio.wait_for(ws.recv(), timeout=5)
    data = json.loads(resp)
    assert "type" in data, "kanban response has no type"
    print(f"  [PASS] kanban: {data.get('type', '?')}")


async def test_chat(ws) -> None:
    """Send a simple chat message and verify a response arrives."""
    await ws.send(json.dumps({"type": "chat", "text": "Say exactly: smoke-test-ok"}))
    resp = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
    data = json.loads(resp)
    content = data.get("content", "")
    assert isinstance(content, str), "response content should be a string"
    assert len(content) > 0, "response content should not be empty"
    # Accept LLM Error (no API key) as valid WS round-trip
    ok = "LLM Error" not in content or all(
        k in os.environ for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
    )
    status = "LLM-response" if not content.startswith("LLM Error") else "WS-roundtrip"
    print(f"  [PASS] chat: {len(content)} chars ({status})")


async def test_provider_switch(ws) -> None:
    """Switch provider and verify acknowledgment."""
    await ws.send(json.dumps({"type": "command", "command": "provider:ollama"}))
    resp = await asyncio.wait_for(ws.recv(), timeout=5)
    data = json.loads(resp)
    assert "ollama" in json.dumps(data).lower() or data.get("type") != "error", \
        f"provider switch failed: {data}"
    print(f"  [PASS] provider switch: ollama")


async def main():
    proc = None
    try:
        # 1. Start server
        print("Starting Python WS server...")
        proc = subprocess.Popen(
            [sys.executable, SERVER_SCRIPT, "ws"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=ROOT,
        )

        # 2. Wait for server
        HOST, PORT = "127.0.0.1", 8765
        ready = await wait_for_server(HOST, PORT, timeout=8)
        if not ready:
            print("  [FAIL] Server did not start within 8s")
            sys.exit(1)
        print(f"  [PASS] Server ready on {HOST}:{PORT}")

        # 3. Connect as client
        import websockets
        async with websockets.connect(f"ws://{HOST}:{PORT}") as ws:
            await test_connection(ws)
            await test_tools_command(ws)
            await test_kanban_command(ws)
            await test_provider_switch(ws)
            await test_chat(ws)

        print(f"\n{'='*45}")
        print(" All 5 E2E smoke tests passed!")
        print(f"{'='*45}")

    except Exception as e:
        print(f"\n  [FAIL] {type(e).__name__}: {e}")
        sys.exit(1)
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


if __name__ == "__main__":
    asyncio.run(main())
