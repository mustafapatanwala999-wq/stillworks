"""Run an agent over the tasks and grade it from ledger state.

    python -m apbench.run --agent reference
    python -m apbench.run --agent claude --model claude-opus-5-5 --tasks T05,T17
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .tasks import BY_ID, TASKS
from .tools import Env
from .verify import snapshot, verify


def expected_state(task) -> dict:
    env = Env()
    for name, args in task.solution:
        result = env.call(name, args)
        if "error" in result:
            raise RuntimeError(f"{task.id} ground truth failed at {name}: {result['error']}")
    state = snapshot(env.conn)
    env.close()
    return state


def run_task(task, agent) -> dict:
    env = Env()
    seed = snapshot(env.conn)
    error = None
    try:
        agent.run(task, env)
    except Exception as exc:  # an agent crash is a result for this task, not a harness failure
        error = f"{type(exc).__name__}: {exc}"
    graded = verify(expected_state(task), snapshot(env.conn), seed)
    env.close()
    outcome = env.finished["outcome"] if env.finished else None
    return {
        "id": task.id,
        "title": task.title,
        **graded,
        "finish_outcome": outcome,
        # The agent reported success while the ledger is wrong.
        "silent_failure": not graded["passed"] and outcome == "completed",
        "steps": len(env.trajectory),
        "error": error,
        "trajectory": env.trajectory,
    }


def summarize(results) -> dict:
    return {
        "tasks": len(results),
        "passed": sum(r["verdict"] == "pass" for r in results),
        "unsafe": sum(r["verdict"] == "unsafe" for r in results),
        "incorrect": sum(r["verdict"] == "incorrect" for r in results),
        "silent_failures": sum(r["silent_failure"] for r in results),
    }


def make_agent(args):
    if args.agent == "reference":
        from .agents.reference import ReferenceAgent
        return ReferenceAgent()
    if args.agent == "naive":
        from .agents.naive import NaiveAgent
        return NaiveAgent()
    from .agents.claude import ClaudeAgent
    return ClaudeAgent(model=args.model, effort=None if args.effort == "none" else args.effort)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agent", choices=["reference", "naive", "claude"], required=True)
    parser.add_argument("--model", default="claude-opus-5-5", help="model id for --agent claude")
    parser.add_argument("--effort", default="high",
                        help="effort for --agent claude; 'none' omits the parameter")
    parser.add_argument("--tasks", help="comma-separated task ids (default: all)")
    parser.add_argument("--out", help="results file (default: results/<agent>.json)")
    args = parser.parse_args(argv)

    tasks = [BY_ID[task_id] for task_id in args.tasks.split(",")] if args.tasks else list(TASKS)
    agent = make_agent(args)

    results = []
    for task in tasks:
        result = run_task(task, agent)
        results.append(result)
        print(f"{result['id']}  {result['verdict'].upper():<9}  {result['title']}")
        for finding in result["findings"]:
            print(f"       - {finding}")
        if result["error"]:
            print(f"       ! agent error: {result['error']}")

    summary = summarize(results)
    print(f"\nagent: {agent.name}")
    print(f"passed {summary['passed']}/{summary['tasks']}   unsafe {summary['unsafe']}   "
          f"incorrect {summary['incorrect']}   silent failures {summary['silent_failures']}")

    out = Path(args.out or f"results/{agent.name}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"agent": agent.name, "summary": summary, "tasks": results}, indent=2),
                   encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
