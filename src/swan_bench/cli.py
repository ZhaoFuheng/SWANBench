"""`swan-bench` command line: prepare the data, run a system, compare runs, inspect questions."""

import argparse
import json
import os
import sys

from . import paths
from .systems import SYSTEMS

DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_ENDPOINT = "http://localhost:4001"  # the SWAN-AISQL cache proxy


def _load_dotenv() -> None:
    path = paths.ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _cmd_prepare(args) -> None:
    from .prepare import prepare

    prepare(force=args.force, masked=not args.no_masked)


def _cmd_questions(args) -> None:
    from .data import load_questions

    for q in load_questions(args.db):
        print(f"{q.qid:24} [{q.difficulty:11}] {q.question}")


def _cmd_run(args) -> None:
    if args.system == "lotus" and os.environ.get("PYTHONHASHSEED") != "0":
        # LOTUS renders prompts in set order: pin the hash seed so prompts (and cache keys) are stable
        os.environ["PYTHONHASHSEED"] = "0"
        os.execv(sys.executable, [sys.executable, "-m", "swan_bench"] + sys.argv[1:])
    from .harness import run

    _load_dotenv()
    options = {"api_key": os.environ.get("OPENAI_API_KEY"), "concurrency": args.concurrency}
    if args.system == "aisql":
        settings = {"ai_concurrency": args.concurrency, **dict(s.split("=", 1) for s in args.set or [])}
        options.update(duckdb_bin=args.duckdb_bin, mock=args.stub, settings=settings)
    if args.system == "plop":
        options.update(plop_bin=args.plop_bin, duckdb_bin=args.duckdb_bin)
    if args.system == "thalamusdb":
        options.update(thalamusdb_python=args.thalamusdb_python, duckdb_bin=args.duckdb_bin)
    if args.system == "palimpzest":
        options.update(palimpzest_python=args.palimpzest_python, optimizer=args.pz_optimizer, policy=args.pz_policy)
    endpoint = None if args.stub else args.endpoint
    tag = args.tag or (f"{args.model.replace('/', '_')}" + ("_stub" if args.stub else ""))
    run(args.system, args.model, endpoint, databases=args.db or paths.DATABASES, qids=set(args.qid or []),
        tag=tag, rerun=args.rerun, retry_errors=args.retry_errors, budget=args.budget, **options)


def _cmd_lint(args) -> None:
    from .aisql import lint
    from .data import load_query, load_questions, query_path

    bad = 0
    for q in load_questions(args.db):
        if args.qid and q.qid not in args.qid:
            continue
        if not query_path("aisql", q.qid).is_file():
            print(f"{q.qid:24} missing {query_path('aisql', q.qid).relative_to(paths.ROOT)}")
            bad += 1
            continue
        for problem in lint(load_query("aisql", q.qid)):
            print(f"{q.qid:24} {problem}")
            bad += 1
    print(f"{bad} problem(s)")
    raise SystemExit(1 if bad else 0)


def _cmd_check(args) -> None:
    from .data import load_questions
    from .equivalence import check

    questions = [q for db in (args.db or paths.DATABASES) for q in load_questions(db)
                 if not args.qid or q.qid in args.qid]
    bad = 0
    for r in check(questions, args.duckdb_bin):
        calls = "  ".join(f"{s}={r[s].get('calls', 'ERR')}" for s in ("aisql", "blendsql", "lotus") if s in r)
        status = "equal" if r["equal"] else "DIFFERENT"
        print(f"{r['qid']:24} {status:9}  calls: {calls}", flush=True)
        if not r["equal"]:
            bad += 1
            for s in ("aisql", "blendsql", "lotus"):
                if "error" in r.get(s, {}):
                    print(f"    {s}: {r[s]['error'][:200]}")
            if "sample" in r:
                print(f"    rows: {r['sample']}")
    print(f"{bad} question(s) differ")
    raise SystemExit(1 if bad else 0)


def _cmd_rescore(args) -> None:
    from .harness import rescore

    o = rescore(args.system, args.tag)["overall"]
    print(f"{args.system}/{args.tag}: quality {o['quality']}, {o['correct']}/{o['total']} exact")


def _cmd_report(args) -> None:
    rows = []
    for scores in sorted(paths.RUNS.glob("*/*/scores.json")):
        report = json.loads(scores.read_text())
        if args.tag and scores.parent.name != args.tag:
            continue
        for db, s in [*report["databases"].items(), ("overall", report["overall"])]:
            rows.append((report["system"], scores.parent.name, db, s))
    print(f"{'system':9} {'run':18} {'database':20} {'quality':>7} {'exact':>8} {'calls':>8} {'fresh':>7} "
          f"{'tokens':>11} {'cost $':>8} {'seconds':>9}")
    for system, tag, db, s in rows:
        tokens = (s["prompt_tokens"] or 0) + (s["completion_tokens"] or 0)
        qual = f"{s['quality']:.3f}" if s.get("quality") is not None else "-"
        if s.get("unsupported"):  # macro over all questions, then over the ones the system's dialect expresses
            qual += f" ({s['quality_supported']:.3f}/{s['total'] - s['unsupported']}q)"
        print(f"{system:9} {tag:18} {db:20} {qual:>7} {s['correct']:>3}/{s['total']:<4} "
              f"{s['requests'] or 0:8} {s['fresh_calls'] if s['fresh_calls'] is not None else '-':>7} "
              f"{tokens:11} {s['cost_usd'] or 0:8.2f} {s['seconds'] or 0:9.0f}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="swan-bench", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="unzip the databases and build their masked copies")
    p.add_argument("--force", action="store_true", help="rebuild even if the files exist")
    p.add_argument("--no-masked", action="store_true", help="only unzip the original databases")
    p.set_defaults(func=_cmd_prepare)

    p = sub.add_parser("questions", help="list the benchmark questions")
    p.add_argument("--db", choices=paths.DATABASES)
    p.set_defaults(func=_cmd_questions)

    p = sub.add_parser("run", help="run one system over the benchmark and score it")
    p.add_argument("--system", required=True, choices=SYSTEMS)
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"chat model (default {DEFAULT_MODEL})")
    p.add_argument("--endpoint", default=os.environ.get("SWAN_BENCH_ENDPOINT", DEFAULT_ENDPOINT),
                   help="OpenAI-compatible endpoint, normally the SWAN-AISQL cache proxy "
                        f"(default $SWAN_BENCH_ENDPOINT or {DEFAULT_ENDPOINT})")
    p.add_argument("--db", action="append", choices=paths.DATABASES, help="repeatable; default all four")
    p.add_argument("--qid", action="append", help="run only this question (repeatable)")
    p.add_argument("--tag", help="run directory under runs/<system>/ (default: the model name)")
    p.add_argument("--rerun", action="store_true", help="discard the run's earlier records and start over")
    p.add_argument("--retry-errors", action="store_true", help="rerun the questions whose earlier record is an error")
    p.add_argument("--budget", type=float, help="stop when the proxy's new spend in this run exceeds this many USD")
    p.add_argument("--stub", action="store_true",
                   help="no model: a local stub answers (SWAN-AISQL uses its built-in mock); checks the queries run")
    p.add_argument("--concurrency", type=int, default=20, help="LLM requests in flight (default 20, every system)")
    p.add_argument("--duckdb-bin", default=os.environ.get("SWAN_AISQL_DUCKDB"),
                   help="aisql: the SWAN-AISQL duckdb binary (default $SWAN_AISQL_DUCKDB)")
    p.add_argument("--set", action="append", metavar="NAME=VALUE", help="aisql: an extra SET before each query")
    p.add_argument("--plop-bin", default=os.environ.get("SWAN_PLOP_BIN"),
                   help="plop: the Morrila fork's duckdb shell (default $SWAN_PLOP_BIN); needs --duckdb-bin for the parquet export")
    p.add_argument("--palimpzest-python", default=os.environ.get("SWAN_PALIMPZEST_PYTHON"),
                   help="palimpzest: the interpreter with palimpzest installed (default .venv-palimpzest/bin/python, see scripts/setup_palimpzest.sh)")
    p.add_argument("--pz-optimizer", default="pareto", choices=["pareto", "none"],
                   help="palimpzest: Abacus's pareto plan search (default) or no optimizer (none)")
    p.add_argument("--pz-policy", default="MaxQuality", choices=["MaxQuality", "MinCost"], help="palimpzest: the optimizer's policy")
    p.add_argument("--thalamusdb-python", default=os.environ.get("SWAN_THALAMUSDB_PYTHON"),
                   help="thalamusdb: the interpreter with thalamusdb installed (default .venv-thalamusdb/bin/python, "
                        "see scripts/setup_thalamusdb.sh); needs --duckdb-bin for the parquet export")
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("lint", help="check that every AISQL query is in the SWAN 2.0 prompt form")
    p.add_argument("--db", choices=paths.DATABASES)
    p.add_argument("--qid", action="append")
    p.set_defaults(func=_cmd_lint)

    p = sub.add_parser("check", help="run every system on the shadow model; their results must be identical")
    p.add_argument("--db", action="append", choices=paths.DATABASES)
    p.add_argument("--qid", action="append")
    p.add_argument("--duckdb-bin", default=os.environ.get("SWAN_AISQL_DUCKDB"))
    p.set_defaults(func=_cmd_check)

    p = sub.add_parser("rescore", help="recompute a finished run's scores from its stored answers")
    p.add_argument("--system", required=True, choices=SYSTEMS)
    p.add_argument("--tag", required=True)
    p.set_defaults(func=_cmd_rescore)

    p = sub.add_parser("report", help="tabulate the scores of finished runs")
    p.add_argument("--tag", help="only runs with this tag")
    p.set_defaults(func=_cmd_report)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
