"""Run an agent over the tasks and grade it from ledger state.

    python -m apbench.run --agent reference
    python -m apbench.run --agent claude --tasks T05,T17
    python -m apbench.run --agent gemini --model <model id> --resume

Results are written after every task. If the model cannot be reached (bad key,
exhausted quota, network), the run stops without scoring the task it was on, and
`--resume` picks up from the tasks already saved.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from .tasks import BY_ID, TASKS
from .tools import Env, InfraError
from .verify import snapshot, verify


def expected_state(task) -> dict:
    env = Env()
    try:
        for name, args in task.solution:
            result = env.call(name, args)
            if "error" in result:
                raise RuntimeError(f"{task.id} ground truth failed at {name}: {result['error']}")
        return snapshot(env.conn)
    finally:
        env.close()


def run_task(task, agent) -> dict:
    """Run one task. An InfraError propagates: it says nothing about the model."""
    env = Env()
    try:
        seed = snapshot(env.conn)
        error = None
        try:
            agent.run(task, env)
        except InfraError:
            raise
        except Exception as exc:  # an agent crash is a result for this task, not a harness failure
            error = f"{type(exc).__name__}: {exc}"
        graded = verify(expected_state(task), snapshot(env.conn), seed)
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
    finally:
        env.close()


def summarize(results) -> dict:
    return {
        "tasks": len(results),
        "passed": sum(r["verdict"] == "pass" for r in results),
        "unsafe": sum(r["verdict"] == "unsafe" for r in results),
        "incorrect": sum(r["verdict"] == "incorrect" for r in results),
        "silent_failures": sum(r["silent_failure"] for r in results),
    }


def _save(out: Path, agent, tasks, finished: dict, complete: bool):
    ordered = [finished[task.id] for task in tasks if task.id in finished]
    record = {
        "agent": agent.name,
        "meta": getattr(agent, "meta", {}),
        "date": dt.date.today().isoformat(),
        "complete": complete,
        "summary": summarize(ordered),
        "tasks": ordered,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")


def run_all(tasks, agent, out: Path, resume=False, log=print):
    """Returns (results in task order, infra_error message or None)."""
    finished = {}
    if resume and out.exists():
        saved = json.loads(out.read_text(encoding="utf-8"))
        if saved["agent"] != agent.name:
            raise ValueError(f"{out} holds results for {saved['agent']!r}, not {agent.name!r}")
        finished = {r["id"]: r for r in saved["tasks"]}
        log(f"resuming: {len(finished)} tasks already saved")

    infra_error = None
    for task in tasks:
        if task.id in finished:
            continue
        try:
            result = run_task(task, agent)
        except InfraError as exc:
            infra_error = f"stopped at {task.id}: {exc}"
            break
        finished[task.id] = result
        log(f"{result['id']}  {result['verdict'].upper():<9}  {result['title']}")
        for finding in result["findings"]:
            log(f"       - {finding}")
        if result["error"]:
            log(f"       ! agent error: {result['error']}")
        _save(out, agent, tasks, finished, complete=False)

    _save(out, agent, tasks, finished, complete=infra_error is None)
    return [finished[task.id] for task in tasks if task.id in finished], infra_error


def make_agent(args):
    if args.agent == "reference":
        from .agents.reference import ReferenceAgent
        return ReferenceAgent()
    if args.agent == "naive":
        from .agents.naive import NaiveAgent
        return NaiveAgent()
    if args.agent == "gemini":
        if not args.model:
            raise SystemExit("--model is required for --agent gemini. "
                             "List the models your key can use: python -m apbench.agents.gemini")
        from .agents.gemini import GeminiAgent
        return GeminiAgent(model=args.model)
    from .agents.claude import ClaudeAgent
    return ClaudeAgent(model=args.model or "claude-opus-5-5",
                       effort=None if args.effort == "none" else args.effort)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agent", choices=["reference", "naive", "claude", "gemini"], required=True)
    parser.add_argument("--model", help="model id for --agent claude (default claude-opus-5-5) "
                                        "or gemini (required)")
    parser.add_argument("--effort", default="high",
                        help="effort for --agent claude; 'none' omits the parameter")
    parser.add_argument("--tasks", help="comma-separated task ids (default: all)")
    parser.add_argument("--out", help="results file (default: results/<agent>.json)")
    parser.add_argument("--resume", action="store_true", help="skip tasks already in the results file")
    args = parser.parse_args(argv)

    tasks = [BY_ID[task_id] for task_id in args.tasks.split(",")] if args.tasks else list(TASKS)
    agent = make_agent(args)
    out = Path(args.out or f"results/{agent.name}.json")

    results, infra_error = run_all(tasks, agent, out, resume=args.resume)

    summary = summarize(results)
    print(f"\nagent: {agent.name}")
    print(f"passed {summary['passed']}/{summary['tasks']}   unsafe {summary['unsafe']}   "
          f"incorrect {summary['incorrect']}   silent failures {summary['silent_failures']}")
    print(f"wrote {out}")
    if infra_error:
        print(f"\nRUN INCOMPLETE - {infra_error}")
        print("The task it was on was not scored. Fix the cause, then rerun with --resume.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
