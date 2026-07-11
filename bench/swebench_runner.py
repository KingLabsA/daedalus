#!/usr/bin/env python3
"""SWE-bench Lite prediction runner for Daedalus.

Generates predictions.jsonl in the official SWE-bench format by letting the
Daedalus agent work each issue inside a checkout of the target repo at its
base commit. Scoring happens afterwards with the official harness (Docker):

    pip install "daedalus-ai[dev]" datasets
    python bench/swebench_runner.py --limit 25 --provider fable
    # then evaluate (official harness, needs Docker):
    #   pip install swebench
    #   python -m swebench.harness.run_evaluation \
    #       --dataset_name princeton-nlp/SWE-bench_Lite \
    #       --predictions_path bench/predictions.jsonl \
    #       --run_id daedalus-v2

Honest expectations: results track the routed model. Local 7-8B models will
score in single digits; use a strong provider for a publishable number. Budget
~2-10 min per instance.

No Anthropic key? Use OpenCode Zen (frontier models via one key):
    export OPENCODE_API_KEY=...        # from opencode.ai/zen
    # optional: export OPENCODE_MODEL=claude-sonnet-4  (or another Zen model)
    python bench/swebench_runner.py --limit 10 --provider opencode
Or the already-running local gateway:
    python bench/swebench_runner.py --limit 10 --provider freellmapi
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKDIR = Path(os.getenv("SWEBENCH_WORKDIR", "/tmp/daedalus_swebench"))


def sh(cmd, cwd=None, timeout=600):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def checkout(instance, cache: Path) -> Path:
    repo = instance["repo"]                      # e.g. "django/django"
    base = instance["base_commit"]
    mirror = cache / repo.replace("/", "__")
    if not mirror.exists():
        print(f"  cloning {repo} ...")
        r = sh(["git", "clone", "--quiet", f"https://github.com/{repo}.git", str(mirror)], timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"clone failed: {r.stderr[:300]}")
    work = WORKDIR / instance["instance_id"]
    if work.exists():
        sh(["rm", "-rf", str(work)])
    sh(["git", "clone", "--quiet", "--shared", str(mirror), str(work)])
    r = sh(["git", "checkout", "--quiet", base], cwd=work)
    if r.returncode != 0:
        raise RuntimeError(f"checkout {base} failed: {r.stderr[:300]}")
    return work


def run_instance(instance, provider: str, max_iters: int) -> str:
    """Returns the model patch (git diff) produced by the agent, '' on failure."""
    work = checkout(instance, WORKDIR / "_mirrors")
    cwd = os.getcwd()
    os.chdir(work)
    try:
        os.environ["SAFETY_MODE"] = "auto"
        os.environ["HERMES_SUBCONSCIOUS"] = "off"
        if provider:
            os.environ["HERMES_AUTO_ROUTE"] = "off"
            os.environ["LLM_PROVIDER"] = provider
        # fresh import per process would be cleaner; acceptable for a runner
        from agent_ultimate import UltimateAgent
        agent = UltimateAgent()
        if provider:
            agent.provider = provider
            agent._provider_pinned = True
        prompt = (
            "You are working in a git checkout of this repository. Fix the following "
            "GitHub issue. Edit only the necessary source files (do NOT edit tests). "
            "Verify your change compiles/imports where practical.\n\n"
            f"ISSUE:\n{instance['problem_statement'][:6000]}"
        )
        agent.convo = []
        agent.converse(prompt, max_iters=max_iters)
        agent.subconscious.stop()
        diff = sh(["git", "diff"], cwd=work).stdout
        return diff
    finally:
        os.chdir(cwd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--provider", default="", help="pin one provider (else auto-routing)")
    ap.add_argument("--max-iters", type=int, default=15)
    ap.add_argument("--out", default=str(ROOT / "bench" / "predictions.jsonl"))
    args = ap.parse_args()

    try:
        from datasets import load_dataset
    except ImportError:
        sys.exit("pip install datasets")
    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    WORKDIR.mkdir(parents=True, exist_ok=True)

    out = Path(args.out)
    done = set()
    if out.exists():
        done = {json.loads(l)["instance_id"] for l in out.read_text().splitlines() if l.strip()}

    todo = [ds[i] for i in range(args.offset, min(args.offset + args.limit, len(ds)))]
    print(f"instances: {len(todo)} (skipping {sum(1 for t in todo if t['instance_id'] in done)} already done)")

    with out.open("a") as fh:
        for i, inst in enumerate(todo, 1):
            iid = inst["instance_id"]
            if iid in done:
                continue
            t0 = time.time()
            try:
                patch = run_instance(inst, args.provider, args.max_iters)
            except Exception as exc:
                print(f"[{i}/{len(todo)}] {iid}: ERROR {exc}")
                patch = ""
            fh.write(json.dumps({
                "instance_id": iid,
                "model_name_or_path": f"daedalus-v2-{args.provider or 'autoroute'}",
                "model_patch": patch,
            }) + "\n")
            fh.flush()
            print(f"[{i}/{len(todo)}] {iid}: {'patch ' + str(len(patch)) + 'B' if patch else 'EMPTY'} in {time.time()-t0:.0f}s")

    print(f"\npredictions -> {out}\nEvaluate with the official swebench harness (see module docstring).")


if __name__ == "__main__":
    main()
