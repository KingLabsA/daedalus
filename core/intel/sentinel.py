"""WorldModelSentinel — watches write tools and injects blast-radius warnings
into context before the next LLM call. Uses its own markers so it never collides
with the ContextEngine's injection block.
"""

import re

WM_BEGIN = "<!--HERMES:WM:BEGIN-->"
WM_END = "<!--HERMES:WM:END-->"
_WM_RE = re.compile(re.escape(WM_BEGIN) + r".*?" + re.escape(WM_END), re.DOTALL)

WRITE_TOOLS = ("write_file", "edit_file_line", "append_file")


class WorldModelSentinel:
    def __init__(self, world_model, write_tools=WRITE_TOOLS):
        self.world_model = world_model
        self.write_tools = write_tools
        self._pending: dict[str, str] = {}
        self._registered = []

    def attach(self, hook_manager):
        pairs = [("pre_tool", self._on_pre_tool), ("pre_llm", self._on_pre_llm)]
        for event, handler in pairs:
            hook_manager.register(event, handler)
        self._registered = [(hook_manager, e, h) for e, h in pairs]

    def detach(self):
        for hook_manager, event, handler in self._registered:
            hook_manager.unregister(event, handler)
        self._registered = []

    def _on_pre_tool(self, calls: list[dict] | None = None, **kwargs):
        try:
            for call in calls or []:
                if call.get("name") not in self.write_tools:
                    continue
                filepath = (call.get("args") or {}).get("filepath", "")
                if not filepath or filepath in self._pending:
                    continue
                warning = self.world_model.render_warning(filepath)
                if warning:
                    self._pending[filepath] = warning
        except Exception:
            pass

    def _on_pre_llm(self, messages: list[dict] | None = None, **kwargs):
        try:
            if not isinstance(messages, list) or not messages:
                return
            if messages[0].get("role") != "system":
                if not self._pending:
                    return
                messages.insert(0, {"role": "system", "content": ""})
            base = str(messages[0].get("content") or "")
            if WM_BEGIN in base:
                base = _WM_RE.sub("", base).rstrip()
            if self._pending:
                block = "\n".join(self._pending.values())
                base = base + f"\n\n{WM_BEGIN}\n{block}\n{WM_END}"
                self._pending = {}
            messages[0]["content"] = base
        except Exception:
            pass
