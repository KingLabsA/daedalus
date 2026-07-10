"""WebSocket server — extracted from the agent monolith (modularization slice 2).

Standalone: agent-resident helpers arrive via an injected `ops` namespace
(built by agent_ultimate), so this module imports nothing from the monolith.
"""
import asyncio
import json
import os

from core.changeset import safe_repo_path
from core.providers import PROVIDER_CONFIGS, _available_providers, _get_cost_summary

WS_HOST = os.getenv("WS_HOST", "127.0.0.1")
WS_PORT = int(os.getenv("WS_PORT", "8765"))

def ws_token_ok(path_or_url: str, required: str) -> bool:
    """WS auth gate: when a token is required, the connection URL must carry
    token=<required> in its query string. No requirement -> always OK."""
    if not required:
        return True
    query = str(path_or_url or "").split("?", 1)[-1] if "?" in str(path_or_url or "") else ""
    pairs = [p.split("=", 1) for p in query.split("&") if "=" in p]
    return any(k == "token" and v == required for k, v in pairs)


class WebSocketServer:
    def __init__(self, agent, ops):
        self.agent = agent; self.ops = ops; self.clients = set()
    async def handler(self, websocket, path=None):
        required_token = os.getenv("HERMES_WS_TOKEN", "")
        if required_token:
            conn_path = path or getattr(websocket, "path", "") or getattr(getattr(websocket, "request", None), "path", "")
            if not ws_token_ok(conn_path, required_token):
                await websocket.close(code=4401, reason="unauthorized: missing/invalid token")
                return
        try:
            origin = websocket.request_headers.get("Origin", "")
            cors_origin = os.getenv("CORS_ORIGIN", "tauri://localhost")
            if origin and cors_origin and origin != cors_origin and origin != "null":
                pass
        except:
            pass
        self.clients.add(websocket)
        try:
            async for message in websocket:
                data = json.loads(message)
                msg_type = data.get("type", "chat")
                if msg_type == "chat":
                    # Full agent loop (auto-routing, failover, multi-iteration tools,
                    # immune system, world model, calibration) with live token streaming.
                    text = data["text"]
                    loop = asyncio.get_event_loop()
                    token_q: "asyncio.Queue" = asyncio.Queue()
                    turn_start = len(self.agent.logs)

                    def _on_tok(t):
                        loop.call_soon_threadsafe(token_q.put_nowait, t)
                    self.agent.on_token = _on_tok

                    async def _drain():
                        while True:
                            tok = await token_q.get()
                            if tok is None:
                                break
                            await websocket.send(json.dumps({"type": "token", "content": tok}))
                    drain_task = asyncio.create_task(_drain())
                    try:
                        result = await loop.run_in_executor(None, self.agent.converse, text)
                    except Exception as e:
                        result = f"Error: {e}"
                    finally:
                        self.agent.on_token = None
                        loop.call_soon_threadsafe(token_q.put_nowait, None)
                    await drain_task
                    self.agent.messages = self.agent.convo
                    self.agent.store.save(self.agent.session_id, self.agent.messages)
                    turn_logs = self.agent.logs[turn_start:]
                    tool_calls = [{"name": l["name"], "args": l.get("args", {})} for l in turn_logs if l.get("type") == "tool_call"]
                    routed = next((l for l in reversed(turn_logs) if l.get("type") == "auto_route"), None)
                    changeset = self.agent.changesets.summary()
                    await websocket.send(json.dumps({
                        "type": "response", "content": result,
                        "toolCalls": tool_calls,
                        "routedTo": routed.get("provider") if routed else self.agent.provider,
                        "changeset": changeset if changeset["files"] else None,
                    }))
                elif msg_type == "file_write":
                    p = safe_repo_path(data.get("path", ""))
                    if not p:
                        await websocket.send(json.dumps({"type":"notification", "content":"Invalid path (outside project)"}))
                    else:
                        try:
                            p.parent.mkdir(parents=True, exist_ok=True)
                            p.write_text(data.get("content", ""))
                            await websocket.send(json.dumps({"type":"file_saved", "data":{"path": data.get("path", "")}}))
                        except OSError as exc:
                            await websocket.send(json.dumps({"type":"notification", "content":f"Save failed: {exc}"}))
                elif msg_type == "command":
                    cmd = data["command"]
                    if cmd == "kanban":
                        await websocket.send(json.dumps({"type":"kanban", "data":self.agent.kanban.get_board_state()}))
                    elif cmd.startswith("kanban:add:"):
                        self.agent.kanban.add_task(cmd.split(":", 2)[2])
                        await websocket.send(json.dumps({"type":"kanban", "data":self.agent.kanban.get_board_state()}))
                    elif cmd.startswith("kanban:move:"):
                        parts = cmd.split(":")
                        self.agent.kanban.move_task(parts[2], parts[3])
                        await websocket.send(json.dumps({"type":"kanban", "data":self.agent.kanban.get_board_state()}))
                    elif cmd.startswith("kanban:remove:"):
                        self.agent.kanban.remove_task(cmd.split(":", 2)[2])
                        await websocket.send(json.dumps({"type":"kanban", "data":self.agent.kanban.get_board_state()}))
                    elif cmd == "tools":
                        await websocket.send(json.dumps({"type":"tools", "data":self.agent.registry.list_tools()}))
                    elif cmd == "skills":
                        await websocket.send(json.dumps({"type":"skills", "data":self.ops.SelfLearner.load_skills()}))
                    elif cmd == "plugins":
                        await websocket.send(json.dumps({"type":"plugins", "data":self.ops.PluginMarketplace.discover_local()}))
                    elif cmd == "remote-plugins":
                        await websocket.send(json.dumps({"type":"plugins", "data":self.ops.PluginMarketplace.list_remote()}))
                    elif cmd.startswith("install-plugin:"):
                        url = cmd.split(":", 1)[1]
                        result = self.ops.PluginMarketplace.install_from_url(url)
                        await websocket.send(json.dumps({"type":"notification", "content":result}))
                    elif cmd.startswith("skill-versions:"):
                        name = cmd.split(":", 1)[1]
                        await websocket.send(json.dumps({"type":"skill-versions", "data":self.ops.PluginMarketplace.get_skill_versions(name)}))
                    elif cmd == "models":
                        cur = self.agent.provider
                        cfg = PROVIDER_CONFIGS.get(cur, {})
                        models = cfg.get("models", [cfg.get("default_model", "unknown")])
                        await websocket.send(json.dumps({"type":"models", "data":{"provider": cur, "models": models, "current": os.environ.get("MODEL_NAME", cfg.get("default_model", ""))}}))
                    elif cmd.startswith("provider:") and not cmd.startswith("provider:test:"):
                        p = cmd.split(":")[1]
                        if p in PROVIDER_CONFIGS:
                            self.agent.provider = p
                            self.agent._provider_pinned = True
                            await websocket.send(json.dumps({"type":"provider", "data":p}))
                        else:
                            await websocket.send(json.dumps({"type":"notification", "content":f"Unknown provider: {p}"}))
                    elif cmd == "logs":
                        await websocket.send(json.dumps({"type":"logs", "data":self.agent.logs[-200:]}))
                    elif cmd == "undo":
                        result = self.ops.git_undo()
                        await websocket.send(json.dumps({"type":"notification", "content":result}))
                        await websocket.send(json.dumps({"type":"diff", "data":self.ops._git_diff()}))
                    elif cmd == "sessions":
                        sessions = self.agent.store.list_sessions()
                        await websocket.send(json.dumps({"type":"sessions", "data":sessions}))
                    elif cmd.startswith("session:load:"):
                        sid = cmd.split(":", 2)[2]
                        msgs = self.agent.store.load(sid)
                        if msgs:
                            self.agent.messages = msgs
                            await websocket.send(json.dumps({"type":"notification", "content":f"Loaded session {sid}"}))
                        else:
                            await websocket.send(json.dumps({"type":"notification", "content":f"Session {sid} not found"}))
                    elif cmd == "session:save":
                        self.agent.store.save(self.agent.session_id, self.agent.messages)
                        await websocket.send(json.dumps({"type":"notification", "content":f"Session saved: {self.agent.session_id}"}))
                    elif cmd == "logs:clear":
                        self.agent.logs.clear()
                        await websocket.send(json.dumps({"type":"logs", "data":[]}))
                    elif cmd.startswith("diff:"):
                        path = cmd.split(":", 1)[1] if ":" in cmd else ""
                        diff = self.ops._git_diff(path)
                        await websocket.send(json.dumps({"type":"diff", "data":diff}))
                    elif cmd == "diff":
                        diff = self.ops._git_diff()
                        await websocket.send(json.dumps({"type":"diff", "data":diff}))
                    elif cmd.startswith("lsp:"):
                        target = cmd.split(":", 1)[1]
                        diag = self.ops._pyright_diagnostics(target)
                        await websocket.send(json.dumps({"type":"lsp", "data":diag}))
                    elif cmd == "lsp":
                        diag = self.ops._pyright_diagnostics()
                        await websocket.send(json.dumps({"type":"lsp", "data":diag}))
                    elif cmd.startswith("provider:test:"):
                        p = cmd.split(":", 2)[2]
                        result = self.ops.test_provider(p)
                        await websocket.send(json.dumps({"type":"provider_test_result", "data":result}))
                    elif cmd == "cost":
                        await websocket.send(json.dumps({"type":"cost", "data":_get_cost_summary()}))
                    elif cmd == "memory":
                        await websocket.send(json.dumps({"type":"memory", "data":self.agent.context.stats()}))
                    elif cmd.startswith("memory:search:"):
                        q = cmd.split(":", 2)[2]
                        hits = self.agent.context.recall(q)
                        await websocket.send(json.dumps({"type":"memory", "data":[{"kind": h["kind"], "content": h["content"]} for h in hits]}))
                    elif cmd == "dream":
                        report = self.agent.dreamer.dream(self.agent._recent_sessions(), use_llm=True)
                        await websocket.send(json.dumps({"type":"dream", "data":report}))
                    elif cmd == "distill":
                        await websocket.send(json.dumps({"type":"distill", "data":self.agent.distiller.distill()}))
                    elif cmd == "subconscious":
                        await websocket.send(json.dumps({"type":"subconscious", "data":self.agent.subconscious.status()}))
                    elif cmd == "experts":
                        await websocket.send(json.dumps({"type":"experts", "data":{"available": _available_providers(), "profiles": self.agent.orchestra.profiles}}))
                    elif cmd.startswith("blast:"):
                        target = cmd.split(":", 1)[1]
                        await websocket.send(json.dumps({"type":"blast", "data":self.agent.world_model.blast_radius(target)}))
                    elif cmd == "doctor":
                        await websocket.send(json.dumps({"type":"doctor", "data":self.agent.doctor.scan()}))
                    elif cmd == "advisor":
                        await websocket.send(json.dumps({"type":"advisor", "data":self.agent.model_advisor.advise()}))
                    elif cmd == "profile":
                        await websocket.send(json.dumps({"type":"profile", "data":self.agent.profiler.load()}))
                    elif cmd.startswith("profile:build:"):
                        answers = json.loads(cmd.split(":", 2)[2])
                        profile = self.agent.profiler.build(answers)
                        await websocket.send(json.dumps({"type":"profile", "data":profile}))
                    elif cmd.startswith("mcp:tools:"):
                        server_name = cmd.split(":", 2)[2]
                        try:
                            await websocket.send(json.dumps({"type":"mcp_tools", "data":self.agent.mcp.list_tools(server_name)}))
                        except Exception as e:
                            await websocket.send(json.dumps({"type":"notification", "content":f"MCP error: {e}"}))
                    elif cmd.startswith("changeset:old:"):
                        _, _, cs_id, cs_path = cmd.split(":", 3)
                        old = self.agent.changesets.original(cs_id, cs_path)
                        await websocket.send(json.dumps({"type":"changeset_old", "data":{"id": cs_id, "path": cs_path, "old": old}}))
                    elif cmd == "changeset:list":
                        await websocket.send(json.dumps({"type":"changesets", "data":self.agent.changesets.list_turns()}))
                    elif cmd.startswith("changeset:accept:"):
                        _, _, cs_id, cs_path = cmd.split(":", 3)
                        note = self.agent.changesets.accept(cs_id, cs_path)
                        await websocket.send(json.dumps({"type":"changeset_update", "data":{"id": cs_id, "note": note, **self.agent.changesets.summary(cs_id)}}))
                    elif cmd.startswith("changeset:accept_hunk:"):
                        _, _, cs_id, hunk_i, cs_path = cmd.split(":", 4)
                        note = self.agent.changesets.accept_hunk(cs_id, cs_path, int(hunk_i))
                        await websocket.send(json.dumps({"type":"changeset_update", "data":{"id": cs_id, "note": note, **self.agent.changesets.summary(cs_id)}}))
                    elif cmd.startswith("changeset:reject_hunk:"):
                        _, _, cs_id, hunk_i, cs_path = cmd.split(":", 4)
                        note = self.agent.changesets.reject_hunk(cs_id, cs_path, int(hunk_i))
                        await websocket.send(json.dumps({"type":"changeset_update", "data":{"id": cs_id, "note": note, **self.agent.changesets.summary(cs_id)}}))
                    elif cmd.startswith("changeset:reject:"):
                        _, _, cs_id, cs_path = cmd.split(":", 3)
                        note = self.agent.changesets.reject(cs_id, cs_path)
                        await websocket.send(json.dumps({"type":"changeset_update", "data":{"id": cs_id, "note": note, **self.agent.changesets.summary(cs_id)}}))
                    elif cmd.startswith("file:read:"):
                        rel = cmd.split(":", 2)[2]
                        p = safe_repo_path(rel)
                        if p and p.is_file():
                            try:
                                await websocket.send(json.dumps({"type":"file_content", "data":{"path": rel, "content": p.read_text(errors="replace")[:1_000_000]}}))
                            except OSError as exc:
                                await websocket.send(json.dumps({"type":"notification", "content":f"Read failed: {exc}"}))
                        else:
                            await websocket.send(json.dumps({"type":"notification", "content":f"Not a readable project file: {rel}"}))
                    elif cmd == "cancel":
                        self.agent.cancel_event.set()
                        await websocket.send(json.dumps({"type":"notification", "content":"Cancelling current run..."}))
                    elif cmd.startswith("scaffold:"):
                        from core.scaffold import scaffold
                        _, kind, sname = cmd.split(":", 2)
                        r = scaffold(kind, sname)
                        note = (f"✅ Scaffolded {r['kind']} in {r['dir']}/ ({len(r['files'])} files)\nRun: {r['run']}"
                                if r.get("ok") else f"❌ {r.get('error')}")
                        await websocket.send(json.dumps({"type":"notification", "content":note}))
                        await websocket.send(json.dumps({"type":"command", "command":"files"}))
                    elif cmd.startswith("deploy:"):
                        from core.deploy import plan
                        parts = cmd.split(":")
                        r = plan(parts[2] if len(parts) > 2 and parts[2] else ".", parts[1])
                        await websocket.send(json.dumps({"type":"notification", "content":"🚀 Deploy plan:\n" + json.dumps(r, indent=1)}))
                    elif cmd.startswith("verify:"):
                        from core.evalgate import gate
                        target_dir = cmd.split(":", 1)[1] or "."
                        await websocket.send(json.dumps({"type":"notification", "content":f"⏳ Verifying {target_dir} …"}))
                        g = await asyncio.get_event_loop().run_in_executor(None, gate, target_dir)
                        if not g.get("ok"):
                            note = f"❌ {g.get('error')}"
                        else:
                            icon = "✅" if g["passed"] else "🚫"
                            lines = [f"{'✓' if c['passed'] else '✗'} {c['name']}" for c in g.get("checks", [])]
                            note = f"{icon} {g['verdict']}\n" + "\n".join(lines)
                        await websocket.send(json.dumps({"type":"notification", "content":note}))
                    elif cmd == "mcp":
                        await websocket.send(json.dumps({"type":"mcp", "data":self.agent.mcp.status()}))
                    elif cmd == "calibration":
                        await websocket.send(json.dumps({"type":"calibration", "data":self.agent.tracker.report()}))
                    elif cmd.startswith("route:"):
                        await websocket.send(json.dumps({"type":"route", "data":self.agent.router.route(cmd.split(":", 1)[1])}))
                    elif cmd == "metrics":
                        data = self.agent.telemetry.metrics()
                        data["slowest_tools"] = self.agent.telemetry.slowest_tools()
                        await websocket.send(json.dumps({"type":"metrics", "data":data}))
                    elif cmd == "system_prompt":
                        await websocket.send(json.dumps({"type":"system_prompt", "data":self.agent.system_prompt}))
                    elif cmd.startswith("system_prompt:set:"):
                        new_prompt = cmd.split(":", 2)[2]
                        self.agent.system_prompt = new_prompt
                        await websocket.send(json.dumps({"type":"notification", "content":"System prompt updated"}))
                    elif cmd == "files":
                        result = self.ops.registry.execute("list_files", {"path": ".", "max_depth": "3"})
                        await websocket.send(json.dumps({"type":"files", "data":json.loads(result)}))
                    elif cmd.startswith("files:"):
                        target = cmd.split(":", 1)[1]
                        result = self.ops.registry.execute("list_files", {"path": target, "max_depth": "3"})
                        await websocket.send(json.dumps({"type":"files", "data":json.loads(result)}))
                    elif cmd == "git_branches":
                        result = self.ops.registry.execute("git_branch", {})
                        await websocket.send(json.dumps({"type":"git_branches", "data":result}))
                    elif cmd == "git_log":
                        result = self.ops.registry.execute("git_log", {"n": "20"})
                        await websocket.send(json.dumps({"type":"git_log", "data":result}))
                    elif cmd.startswith("approve:"):
                        wid = cmd.split(":", 1)[1]
                        if self.agent.safety.approve(wid):
                            await websocket.send(json.dumps({"type":"notification", "content":"Approved"}))
                        else:
                            result = self.ops.registry.execute("confirm_write", {"write_id": wid})
                            await websocket.send(json.dumps({"type":"notification", "content":result}))
                    elif cmd.startswith("deny:"):
                        wid = cmd.split(":", 1)[1]
                        if self.agent.safety.deny(wid):
                            await websocket.send(json.dumps({"type":"notification", "content":"Denied"}))
                        else:
                            result = self.ops.registry.execute("deny_write", {"write_id": wid})
                            await websocket.send(json.dumps({"type":"notification", "content":result}))
                    elif cmd == "hooks":
                        await websocket.send(json.dumps({"type":"hooks", "data":self.ops.HookManager.list_hooks()}))
                    elif cmd.startswith("hook:register:"):
                        parts = cmd.split(":", 2)
                        event = parts[1]
                        self.ops.HookManager.register(event, lambda: None)
                        await websocket.send(json.dumps({"type":"notification", "content":f"Registered hook for {event}"}))
                    elif cmd == "checkpoints":
                        cps = self.ops.CheckpointManager.list_checkpoints()
                        transformed = []
                        for cp in cps:
                            transformed.append({
                                "id": cp.get("label", ""),
                                "label": cp.get("label", ""),
                                "timestamp": cp.get("timestamp", ""),
                                "filesChanged": cp.get("files_changed", 0),
                            })
                        await websocket.send(json.dumps({"type":"checkpoints", "data":transformed}))
                    elif cmd.startswith("checkpoint:create:"):
                        label = cmd.split(":", 2)[2] if len(cmd.split(":")) > 2 else ""
                        result = self.ops.CheckpointManager.create_checkpoint(label)
                        await websocket.send(json.dumps({"type":"notification", "content":result}))
                    elif cmd.startswith("checkpoint:restore:"):
                        label = cmd.split(":", 2)[2]
                        result = self.ops.CheckpointManager.restore_checkpoint(label)
                        await websocket.send(json.dumps({"type":"notification", "content":result}))
                    elif cmd.startswith("checkpoint:delete:"):
                        label = cmd.split(":", 2)[2]
                        result = self.ops.CheckpointManager.delete_checkpoint(label)
                        await websocket.send(json.dumps({"type":"notification", "content":result}))
                    elif cmd == "index":
                        result = self.agent.indexer.index_project()
                        await websocket.send(json.dumps({"type":"notification", "content":result}))
                    elif cmd.startswith("search:"):
                        query = cmd.split(":", 1)[1]
                        results = self.agent.indexer.search(query)
                        await websocket.send(json.dumps({"type":"search_results", "data":results}))
                    elif cmd == "index:stats":
                        stats = self.agent.indexer.get_stats()
                        await websocket.send(json.dumps({"type":"index_stats", "data":{
                            "totalFiles": stats.get("total_files", 0),
                            "totalChunks": sum(stats.get("by_extension", {}).values()),
                            "lastUpdated": None,
                        }}))
                    elif cmd.startswith("safety:mode:"):
                        mode = cmd.split(":", 2)[2]
                        self.agent.safety.mode = mode
                        await websocket.send(json.dumps({"type":"safety_mode", "data":mode}))
                    elif cmd == "safety:pending":
                        pending = self.agent.safety.get_pending()
                        transformed = []
                        for p in pending:
                            transformed.append({
                                "id": p.get("id", ""),
                                "tool": p.get("tool", ""),
                                "args": p.get("args", {}),
                                "timestamp": p.get("timestamp", ""),
                            })
                        await websocket.send(json.dumps({"type":"pending_approvals","data":transformed}))
                    elif cmd == "safety:status":
                        await websocket.send(json.dumps({"type":"safety_mode","data":self.agent.safety.mode}))
                    elif cmd.startswith("suggest:confirm:"):
                        wid = cmd.split(":", 2)[2]
                        result = self.ops.registry.execute("confirm_write", {"write_id": wid})
                        await websocket.send(json.dumps({"type":"notification","content":result}))
                    elif cmd.startswith("suggest:deny:"):
                        wid = cmd.split(":", 2)[2]
                        result = self.ops.registry.execute("deny_write", {"write_id": wid})
                        await websocket.send(json.dumps({"type":"notification","content":result}))
                    elif cmd == "index:reindex":
                        result = self.agent.indexer.index_project()
                        await websocket.send(json.dumps({"type":"notification","content":result}))
                    elif cmd.startswith("index:search:"):
                        query = cmd.split(":", 2)[2]
                        results = self.agent.indexer.search(query)
                        await websocket.send(json.dumps({"type":"index_results","data":results}))
                    elif cmd.startswith("model:"):
                        model = cmd.split(":", 1)[1]
                        os.environ["MODEL_NAME"] = model
                        await websocket.send(json.dumps({"type":"model", "data":model}))
                    elif cmd == "watcher:start":
                        result = self.ops.FileWatcher.start(self.agent.indexer)
                        await websocket.send(json.dumps({"type":"notification","content":result}))
                    elif cmd == "watcher:stop":
                        result = self.ops.FileWatcher.stop()
                        await websocket.send(json.dumps({"type":"notification","content":result}))
                    elif cmd == "watcher:status":
                        await websocket.send(json.dumps({"type":"watcher_status","data":self.ops.FileWatcher.status()}))
                    elif cmd.startswith("grep:"):
                        parts = cmd.split(":", 3)
                        pattern = parts[1] if len(parts) > 1 else ""
                        path = parts[2] if len(parts) > 2 else "."
                        result = self.ops.registry.execute("grep", {"pattern": pattern, "path": path})
                        await websocket.send(json.dumps({"type":"grep_results","data":result}))
                    elif cmd.startswith("rename:"):
                        parts = cmd.split(":", 3)
                        old_name = parts[1] if len(parts) > 1 else ""
                        new_name = parts[2] if len(parts) > 2 else ""
                        result = self.ops.registry.execute("rename_symbol", {"old_name": old_name, "new_name": new_name})
                        await websocket.send(json.dumps({"type":"notification","content":result}))
                    elif cmd.startswith("explain:"):
                        code = cmd.split(":", 1)[1]
                        result = self.ops.registry.execute("explain_code", {"code": code})
                        await websocket.send(json.dumps({"type":"explain","data":result}))
                    elif cmd.startswith("review:"):
                        code = cmd.split(":", 1)[1]
                        result = self.ops.registry.execute("review_code", {"code": code})
                        await websocket.send(json.dumps({"type":"review","data":result}))
                    elif cmd.startswith("refactor:"):
                        code = cmd.split(":", 1)[1]
                        result = self.ops.registry.execute("refactor_code", {"code": code})
                        await websocket.send(json.dumps({"type":"refactor","data":result}))
                    elif cmd.startswith("bg:start:"):
                        command = cmd.split(":", 2)[2]
                        result = self.ops.registry.execute("background_process", {"command": command})
                        await websocket.send(json.dumps({"type":"notification","content":result}))
                    elif cmd.startswith("bg:poll:"):
                        pid = cmd.split(":", 2)[2]
                        result = self.ops.registry.execute("poll_process", {"pid": pid})
                        await websocket.send(json.dumps({"type":"bg_output","data":result}))
                    elif cmd.startswith("bg:kill:"):
                        pid = cmd.split(":", 2)[2]
                        result = self.ops.registry.execute("kill_process", {"pid": pid})
                        await websocket.send(json.dumps({"type":"notification","content":result}))
                    elif cmd.startswith("lint:"):
                        path_arg = cmd.split(":", 1)[1] if ":" in cmd else "."
                        result = self.ops.registry.execute("lint_and_test", {"path": path_arg})
                        await websocket.send(json.dumps({"type":"lint_results","data":result}))
                    elif cmd.startswith("task:"):
                        parts = cmd.split(":", 2)
                        action = parts[1] if len(parts) > 1 else "list"
                        arg = parts[2] if len(parts) > 2 else ""
                        result = self.ops.registry.execute("task_board", {"action": action, "task_name": arg})
                        await websocket.send(json.dumps({"type":"task_board","data":result}))
                    elif cmd.startswith("repo:map"):
                        path_arg = cmd.split(":", 2)[2] if cmd.count(":") >= 2 else "."
                        result = self.ops.registry.execute("repo_map", {"path": path_arg})
                        await websocket.send(json.dumps({"type":"repo_map","data":result}))
                    # Send any streamed lines
                    stream = self.ops._drain_stream()
                    if stream:
                        await websocket.send(json.dumps({"type":"stream", "data":stream}))
        except: pass
        finally: self.clients.discard(websocket)
    async def start(self):
        import websockets
        async def _stream_pusher():
            while True:
                await asyncio.sleep(0.5)
                stream = self.ops._drain_stream()
                if stream and self.clients:
                    msg = json.dumps({"type":"stream", "data":stream})
                    await asyncio.gather(*(c.send(msg) for c in self.clients), return_exceptions=True)
        asyncio.ensure_future(_stream_pusher())
        async with websockets.serve(self.handler, WS_HOST, WS_PORT):
            print(f"WebSocket server on ws://{WS_HOST}:{WS_PORT}")
            await asyncio.Future()

