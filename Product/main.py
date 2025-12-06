import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from pipeline import SchedulingPipeline
from storage import RunStorage


def _load_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Input file '{path}' does not exist")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_input(
    *,
    input_path: Optional[str],
    from_run: Optional[str],
    storage: RunStorage
) -> Dict[str, Any]:
    if input_path:
        return _load_json_file(Path(input_path))
    if from_run:
        run = storage.load_run(from_run)
        return run["input"]
    latest = storage.get_latest_run()
    if latest:
        return latest["input"]
    raise ValueError(
        "No input source provided. Supply --input <path.json> or --from-run <run_id>."
    )


def cmd_run(args: argparse.Namespace):
    solver_type = "mock" if args.use_mock_solver else "julia"
    pipeline = SchedulingPipeline(solver_type=solver_type)
    input_data = _resolve_input(
        input_path=args.input,
        from_run=args.from_run,
        storage=pipeline.storage,
    )

    run_id, result = pipeline.run_optimization(input_data, save=not args.no_save)

    print(f"\nRun ID: {run_id}")
    print(f"Status: {result['status']}")
    if result.get("objective_value") is not None:
        print(f"Objective: {result['objective_value']:.3f}")
    if result.get("solve_time_seconds") is not None:
        print(f"Solve Time: {result['solve_time_seconds']:.2f}s")
    print(f"Saved to SQLite: {not args.no_save}")


def cmd_explain(args: argparse.Namespace):
    pipeline = SchedulingPipeline(solver_type="julia")  # Solver not needed for explanation
    explanation = pipeline.explain_run_by_id(args.run_id, question=args.question)
    print(f"\nExplanation for {args.run_id}:\n")
    print(explanation)


def cmd_compare(args: argparse.Namespace):
    pipeline = SchedulingPipeline(solver_type="julia")  # Solver not needed for comparison
    old_run = pipeline.storage.load_run(args.run_id1)
    new_run = pipeline.storage.load_run(args.run_id2)
    explanation = pipeline.explainer.compare_schedules(
        old_run=old_run,
        new_run=new_run,
        question=args.question,
    )
    comparison = pipeline.storage.compare_runs(args.run_id1, args.run_id2)

    print(f"\nComparison between {args.run_id1} and {args.run_id2}:\n")
    print(explanation)
    print("\nChanged assignments:")
    for change in comparison["changed_assignments"]:
        print(f" - {change['course_id']}: {change['change']}")


def cmd_list(args: argparse.Namespace):
    storage = RunStorage()
    runs = storage.get_run_history(limit=args.limit)

    if not runs:
        print("No runs saved yet.")
        return

    for run in runs:
        if args.status and run["status"] != args.status:
            continue
        print(
            f"{run['run_id']} | {run['timestamp']} | status={run['status']} "
            f"| obj={run['objective_value']} | assignments={run['num_assignments']}"
        )


def cmd_stats(_args: argparse.Namespace):
    storage = RunStorage()
    stats = storage.get_run_statistics()
    print(json.dumps(stats, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Course scheduling CLI (no inline sample data)."
    )
    parser.add_argument(
        "--use-mock-solver",
        action="store_true",
        help="Use mock solver instead of Julia solver.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run optimization with JSON input.")
    run_parser.add_argument("--input", help="Path to input JSON file.")
    run_parser.add_argument(
        "--from-run",
        help="Reuse input JSON from an existing run stored in SQLite.",
    )
    run_parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not persist the run in SQLite (useful for dry runs).",
    )
    run_parser.set_defaults(func=cmd_run)

    explain_parser = subparsers.add_parser(
        "explain", help="Generate explanation for a stored run."
    )
    explain_parser.add_argument("--run-id", required=True, help="Run identifier.")
    explain_parser.add_argument("--question", help="Optional custom question.")
    explain_parser.set_defaults(func=cmd_explain)

    compare_parser = subparsers.add_parser(
        "compare", help="Compare two stored runs."
    )
    compare_parser.add_argument("--run-id1", required=True, help="Baseline run.")
    compare_parser.add_argument("--run-id2", required=True, help="New run.")
    compare_parser.add_argument("--question", help="Optional comparison question.")
    compare_parser.set_defaults(func=cmd_compare)

    list_parser = subparsers.add_parser(
        "list", help="List recently saved optimization runs."
    )
    list_parser.add_argument("--limit", type=int, default=20, help="Max runs to show.")
    list_parser.add_argument(
        "--status",
        help="Filter by solver status (e.g., optimal, infeasible).",
    )
    list_parser.set_defaults(func=cmd_list)

    stats_parser = subparsers.add_parser(
        "stats", help="Show aggregate statistics across runs."
    )
    stats_parser.set_defaults(func=cmd_stats)

    return parser


def main(argv: Optional[list[str]] = None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:  # noqa: BLE001 - show meaningful error
        print(f"❌ {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()