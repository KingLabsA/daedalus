# Lazy re-exports (PEP 562) so subpackages like core.context can be imported
# without pulling in agent_ultimate (which would be a circular import once
# agent_ultimate itself imports from core).

_EXPORTS = {
    # From core.tools
    "ToolRegistry": "core.tools",
    "registry": "core.tools",
    "SelfHealer": "core.tools",
    "SelfLearner": "core.tools",
    "HookManager": "core.tools",
    "FileWatcher": "core.tools",
    "CORE_TOOLS": "core.tools",
    "DESTRUCTIVE_TOOLS": "core.tools",
    "BLOCKED_COMMANDS": "core.tools",
    "MAX_FILE_SIZE": "core.tools",
    "PROMPT_INJECTION_PATTERNS": "core.tools",
    # From core.memory
    "SessionStore": "core.memory",
    "compress_messages": "core.memory",
    "PluginMarketplace": "core.memory",
    # From core.kanban
    "KanbanBoard": "core.kanban",
    "KanbanTask": "core.kanban",
    "KanbanWorker": "core.kanban",
    "GoalManager": "core.kanban",
    "SubAgent": "core.kanban",
    "ParallelExecutor": "core.kanban",
}


def __getattr__(name):
    if name in _EXPORTS:
        import importlib

        mod = importlib.import_module(_EXPORTS[name])
        return getattr(mod, name)
    raise AttributeError(f"module 'core' has no attribute {name!r}")


def __dir__():
    return sorted(_EXPORTS)
