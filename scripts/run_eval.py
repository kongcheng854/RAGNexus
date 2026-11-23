"""Command line entry point for validating and scoring a versioned Golden Set."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.golden_dataset import read_json, validate_golden


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["run"] and any(a.split("=")[0] == "--dataset" for a in argv) and not any(
            a.split("=")[0] == "--experiment" for a in argv):
        from scripts.run_retrieval_eval import main as score
        return score(argv[1:], golden_only=True)

    parser = argparse.ArgumentParser(description="正式 Golden Set 校验与检索测评")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-dataset", help="离线校验整个数据集")
    validate.add_argument("--dataset", type=Path, required=True)
    check = commands.add_parser("validate-experiment", help="离线校验实验参数和两组声明差异")
    check.add_argument("--experiment", type=Path, required=True)
    run = commands.add_parser("run", help="按实验执行两组检索，评分并生成报告")
    run.add_argument("--experiment", type=Path, required=True)
    run.add_argument("--base-url", default=os.getenv("RAGNEXUS_BASE_URL", "http://127.0.0.1:8000"))
    run.add_argument("--results-dir", type=Path, default=Path(__file__).resolve().parents[1] / "eval/results")
    for command, help_text in (("score", "从已保存的上下文评分"), ("report", "从已保存的评分生成报告")):
        replay = commands.add_parser(command, help=help_text)
        replay.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-dataset":
            data = read_json(args.dataset)
            validate_golden(data)
            print(f"{data['name']} / {data['version']} 校验通过，共 {len(data['cases'])} 道题。")
        elif args.command == "validate-experiment":
            from scripts.eval_experiment import read_experiment
            experiment = read_experiment(args.experiment)
            print(f"{experiment.experiment_id} 声明校验通过，差异为 {', '.join(experiment.changed_fields)}")
        elif args.command == "run":
            from scripts.eval_runner import execute
            folder, valid = asyncio.run(execute(args.experiment, args.results_dir, args.base_url))
            print(f"报告已写入 {folder / 'report.md'}")
            return 0 if valid else 1
        else:
            from scripts.eval_score import report_run, score_run
            from scripts.eval_artifacts import analysis_folder, run_lock
            with run_lock(args.run_dir):
                if args.command == "score":
                    return 0 if asyncio.run(score_run(args.run_dir)) else 1
                report_run(args.run_dir)
            location = analysis_folder(args.run_dir, read_json(args.run_dir / "manifest.json"))
            print(f"报告已写入 {location / 'report.md'}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"{args.command} 失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
