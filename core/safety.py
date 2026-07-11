"""Safety — plan/act gate for autonomous operations.

Standalone: stdlib only, no agent_ultimate dependency.
"""

import time
from datetime import datetime


class SafetyManager:
    """Plan/Act gate for autonomous operations."""

    def __init__(self, mode: str = "suggest"):
        self.mode = mode  # suggest, plan, auto
        self._pending_approvals: dict[str, dict] = {}

    def should_approve(self, tool_name: str, args: dict) -> tuple[bool, str]:
        """Check if tool execution needs user approval."""
        if self.mode == "auto":
            return True, "auto-mode"

        # Always allow read-only tools
        READ_ONLY = {
            "read_file",
            "list_files",
            "grep",
            "git_status",
            "git_log",
            "git_diff_preview",
            "map_repo",
            "get_time",
            "lsp_diagnostics",
            "web_search",
            "web_fetch",
            "test_provider",
            "list_providers",
            "explain_code",
            "review_code",
            "refactor_code",
        }
        if tool_name in READ_ONLY:
            return True, "read-only"

        # Destructive tools need approval in suggest/plan mode
        DESTRUCTIVE = {"write_file", "edit_file_line", "append_file", "run_command", "docker_execute", "git_commit", "git_push", "git_undo"}
        if tool_name in DESTRUCTIVE and self.mode in ("suggest", "plan"):
            approval_id = f"appr-{int(time.time() * 1000)}"
            self._pending_approvals[approval_id] = {"tool": tool_name, "args": args, "timestamp": datetime.now().isoformat(), "status": "pending"}
            return False, approval_id

        return True, "allowed"

    def approve(self, approval_id: str) -> bool:
        if approval_id in self._pending_approvals:
            self._pending_approvals[approval_id]["status"] = "approved"
            return True
        return False

    def deny(self, approval_id: str) -> bool:
        if approval_id in self._pending_approvals:
            self._pending_approvals[approval_id]["status"] = "denied"
            del self._pending_approvals[approval_id]
            return True
        return False

    def get_pending(self) -> list[dict]:
        return [{"id": k, **v} for k, v in self._pending_approvals.items() if v["status"] == "pending"]
