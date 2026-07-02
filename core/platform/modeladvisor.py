"""ModelAdvisor — what models can THIS machine actually run?

Detects hardware (RAM, cores, Apple Silicon, NVIDIA) and tiers local model
recommendations accordingly; lists cloud models reachable through configured keys.
"""
import json
import os
import platform
import shutil
import subprocess
from typing import Callable, Dict, List, Optional

# (min_ram_gb, [model, ...]) — q4-ish quants; unified memory counts fully on Apple Silicon
LOCAL_TIERS = [
    (4, ["llama3.2:3b", "qwen2.5:3b", "phi3.5:3.8b", "hermes3:3b"]),
    (8, ["qwen2.5-coder:7b", "llama3.1:8b", "hermes3:8b", "king3djbl/mythos-v2-8b-q4", "mistral:7b"]),
    (16, ["qwen2.5-coder:14b", "gemma2:9b", "deepseek-coder-v2:16b", "phi4:14b"]),
    (24, ["qwen2.5:32b", "mixtral:8x7b", "codestral:22b"]),
    (48, ["llama3.1:70b", "hermes3:70b", "qwen2.5:72b"]),
    (128, ["llama3.1:405b (needs cluster/quant)", "hermes3:405b"]),
]

CLOUD_HIGHLIGHTS = {
    "fable": ["claude-fable-5 (Mythos-class)", "claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"],
    "anthropic": ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-5"],
    "openai": ["gpt-4o", "gpt-4o-mini", "o1"],
    "google": ["gemini-1.5-pro", "gemini-1.5-flash"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "groq": ["llama-3.3-70b (fast)", "mixtral-8x7b"],
    "xai": ["grok-2"],
    "mistral": ["mistral-large", "codestral"],
    "openrouter": ["(300+ models via one key)"],
}


def default_spec_probe() -> Dict:
    system = platform.system().lower()
    machine = platform.machine().lower()
    ram_gb = 0.0
    try:
        if system == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
            ram_gb = int(out.stdout.strip()) / 1e9
        elif system == "linux":
            with open("/proc/meminfo") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        ram_gb = int(line.split()[1]) * 1024 / 1e9
                        break
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return {
        "os": system,
        "arch": machine,
        "cpu_cores": os.cpu_count() or 0,
        "ram_gb": round(ram_gb, 1),
        "apple_silicon": system == "darwin" and machine == "arm64",
        "nvidia_gpu": bool(shutil.which("nvidia-smi")),
    }


def default_ollama_list() -> List[str]:
    if not shutil.which("ollama"):
        return []
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
        models = []
        for line in out.stdout.splitlines()[1:]:
            if line.strip():
                models.append(line.split()[0])
        return models
    except (OSError, subprocess.TimeoutExpired):
        return []


class ModelAdvisor:
    def __init__(
        self,
        spec_probe: Callable[[], Dict] = default_spec_probe,
        ollama_list: Callable[[], List[str]] = default_ollama_list,
        provider_configs: Optional[Dict[str, dict]] = None,
        env: Optional[dict] = None,
    ):
        self.spec_probe = spec_probe
        self.ollama_list = ollama_list
        self.provider_configs = provider_configs or {}
        self.env = env if env is not None else dict(os.environ)

    def advise(self) -> Dict:
        specs = self.spec_probe()
        installed = set(self.ollama_list())
        # Apple Silicon unified memory: the GPU shares all RAM; discrete-GPU machines
        # without NVIDIA are effectively CPU-bound, so be conservative.
        usable_gb = specs["ram_gb"] if (specs["apple_silicon"] or specs["nvidia_gpu"]) else specs["ram_gb"] * 0.6
        local: List[Dict] = []
        for min_ram, models in LOCAL_TIERS:
            if usable_gb >= min_ram:
                for model in models:
                    local.append({
                        "model": model,
                        "tier_ram_gb": min_ram,
                        "installed": any(model.split(":")[0] in m for m in installed),
                    })
        cloud = {}
        for name, cfg in self.provider_configs.items():
            env_key = cfg.get("env", "")
            if not env_key or self.env.get(env_key):
                cloud[name] = CLOUD_HIGHLIGHTS.get(name, [cfg.get("default_model", "?")])
        best_local = local[-1]["model"] if local else None
        best_cloud = next((f"{p}: {CLOUD_HIGHLIGHTS[p][0]}" for p in ("fable", "anthropic", "openai", "deepseek", "google") if p in cloud), None)
        return {
            "specs": specs,
            "usable_memory_gb": round(usable_gb, 1),
            "local_models": local,
            "installed_local": sorted(installed),
            "cloud_providers": cloud,
            "recommended": {"local": best_local, "cloud": best_cloud},
        }

    def render(self, advice: Optional[Dict] = None) -> str:
        advice = advice or self.advise()
        specs = advice["specs"]
        lines = [
            f"Machine: {specs['os']}/{specs['arch']}, {specs['cpu_cores']} cores, {specs['ram_gb']} GB RAM"
            + (" (Apple Silicon unified memory)" if specs["apple_silicon"] else "")
            + (" + NVIDIA GPU" if specs["nvidia_gpu"] else ""),
            f"Usable model memory: ~{advice['usable_memory_gb']} GB",
            "",
            "LOCAL (via Ollama):",
        ]
        if advice["local_models"]:
            for item in advice["local_models"]:
                mark = " [installed]" if item["installed"] else ""
                lines.append(f"  - {item['model']} (needs ~{item['tier_ram_gb']} GB){mark}")
        else:
            lines.append("  (not enough RAM detected for local models, or detection failed)")
        lines.append("")
        lines.append("CLOUD (keys configured):")
        if advice["cloud_providers"]:
            for provider, models in advice["cloud_providers"].items():
                lines.append(f"  - {provider}: {', '.join(models[:4])}")
        else:
            lines.append("  (no provider keys configured — run /doctor)")
        rec = advice["recommended"]
        lines.append("")
        lines.append(f"RECOMMENDED -> local: {rec['local'] or 'none'} | cloud: {rec['cloud'] or 'none'}")
        return "\n".join(lines)
