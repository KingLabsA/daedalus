# Lazy re-exports (PEP 562) so subpackages like core.context can be imported
# without pulling in agent_ultimate (which would be a circular import once
# agent_ultimate itself imports from core).

_EXPORTS = {
    "UltimateAgent": "agent_ultimate",
    "ToolRegistry": "agent_ultimate",
    "registry": "agent_ultimate",
    "SessionStore": "agent_ultimate",
    "compress_messages": "agent_ultimate",
    "ProviderRouter": "core.providers",
    "SelfLearner": "agent_ultimate",
    "SelfHealer": "agent_ultimate",
    "SelfVerifier": "agent_ultimate",
    "AdvancedBrowser": "agent_ultimate",
    "DesktopController": "agent_ultimate",
    "KanbanBoard": "agent_ultimate",
    "KanbanTask": "agent_ultimate",
    "KanbanWorker": "agent_ultimate",
    "GoalManager": "agent_ultimate",
    "SubAgent": "agent_ultimate",
    "ParallelExecutor": "agent_ultimate",
    "CheckpointManager": "agent_ultimate",
    "CodebaseIndexer": "agent_ultimate",
    "SafetyManager": "agent_ultimate",
    "HookManager": "agent_ultimate",
    "FileWatcher": "agent_ultimate",
}


def __getattr__(name):
    if name in _EXPORTS:
        import importlib

        mod = importlib.import_module(_EXPORTS[name])
        return getattr(mod, name)
    raise AttributeError(f"module 'core' has no attribute {name!r}")


def __dir__():
    return sorted(_EXPORTS)
