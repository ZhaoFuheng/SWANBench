"""Run one system over the benchmark and score it.

For each question, the gold query runs on the ORIGINAL database and the question's AISQL query, through
the system, on the MASKED one; `quality.py` scores the answer (quality, and the 2024 exact match). A query
that raises scores 0. The system's LLM traffic goes through a `Meter`, so calls, tokens, cost and latency
are measured the same way for every system. Questions run one at a time.

Output, under `runs/<system>/<tag>/`:
- `queries.jsonl`: one record per question, appended as soon as it finishes. A rerun skips the questions
  already recorded, so an interrupted run resumes; `--retry-errors` reruns the questions that raised
  (a later record replaces an earlier one); `--rerun` starts over.
- `scores.json`: per-database and overall quality, exact-match counts and totals, rebuilt from `queries.jsonl`.
"""

import json
import time
import urllib.request
from datetime import datetime, timezone

from . import paths
from .aisql import Unsupported
from .data import load_query, load_questions
from .execution import execute
from .meter import Meter
from .quality import exact_match, quality
from .systems import load_system


def _proxy_spend(endpoint: str | None) -> float | None:
    """Money spent so far at a SWAN-AISQL cache proxy: the recorded cost of every entry in its store."""
    if not endpoint:
        return None
    try:
        with urllib.request.urlopen(endpoint.rstrip("/") + "/cache/stats", timeout=10) as r:
            return float(json.load(r)["cached_cost_usd"])
    except Exception:  # noqa: BLE001 -- not a cache proxy
        return None


def _read_records(path) -> dict:
    records = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                records[r["qid"]] = r
    return records


#: rows kept per answer in queries.jsonl (enough for every gold answer, so `rescore` can recompute scores)
MAX_STORED_ROWS = 5000

SUMMED = ("requests", "fresh_calls", "errors", "prompt_tokens", "completion_tokens", "reasoning_tokens",
          "cost_usd", "seconds")


def summarize(records: list[dict]) -> dict:
    out = {"correct": sum(r["match"] for r in records), "total": len(records),
           "failed": sum(r["error"] is not None and not r.get("unsupported") for r in records)}
    out["accuracy"] = round(out["correct"] / out["total"], 4) if records else None
    scored = [r["quality"] for r in records if r.get("quality") is not None]
    out["quality"] = round(sum(scored) / len(scored), 4) if records and len(scored) == len(records) else None
    # a system whose dialect cannot express a question scores 0 on it; the macro over the rest is kept too
    supported = [r for r in records if not r.get("unsupported")]
    out["unsupported"] = len(records) - len(supported)
    sup_scored = [r["quality"] for r in supported if r.get("quality") is not None]
    out["quality_supported"] = round(sum(sup_scored) / len(sup_scored), 4) if sup_scored and len(sup_scored) == len(supported) else None
    for k in SUMMED:
        vals = [r.get(k) for r in records if r.get(k) is not None]
        out[k] = round(sum(vals), 6) if vals else None
    return out


def write_scores(run_dir, meta: dict) -> dict:
    records = list(_read_records(run_dir / "queries.jsonl").values())
    by_db = {db: summarize([r for r in records if r["db"] == db]) for db in paths.DATABASES}
    report = {**meta, "databases": {db: s for db, s in by_db.items() if s["total"]}, "overall": summarize(records)}
    (run_dir / "scores.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def rescore(system_name: str, tag: str) -> dict:
    """Recompute match and quality of a finished run from its stored rows (no system is run)."""
    run_dir = paths.RUNS / system_name / tag
    records = _read_records(run_dir / "queries.jsonl")
    questions = {q.qid: q for q in load_questions()}
    for qid, r in records.items():
        q = questions[qid]
        if r["error"] is not None:
            r.update(match=False, quality=0.0)
            continue
        if r.get("n_rows", 0) > len(r.get("rows", [])):  # an old record that kept only the first rows
            r["quality"] = None
            continue
        gold = execute(paths.require_database(q.db), q.gold_sql)
        predicted = [tuple(x) for x in r["rows"]]
        r["match"] = exact_match(predicted, gold, q.gold_sql, load_query("aisql", qid))
        for k in ("kind", "value", "rel_err", "precision", "recall"):
            r.pop(k, None)
        r.update(quality(predicted, gold, q.gold_sql, load_query("aisql", qid)))
    with open(run_dir / "queries.jsonl", "w") as f:
        for r in records.values():
            f.write(json.dumps(r, default=str) + "\n")
    old = json.loads((run_dir / "scores.json").read_text()) if (run_dir / "scores.json").is_file() else {}
    meta = {k: v for k, v in old.items() if k not in ("databases", "overall")}
    return write_scores(run_dir, meta)


def run(system_name: str, model: str, endpoint: str | None, databases=paths.DATABASES, qids=None,
        tag: str | None = None, rerun: bool = False, retry_errors: bool = False, budget: float | None = None,
        **options) -> dict:
    system_cls = load_system(system_name)
    run_dir = paths.RUNS / system_name / (tag or model.replace("/", "_"))
    run_dir.mkdir(parents=True, exist_ok=True)
    log = run_dir / "queries.jsonl"
    if rerun:
        log.unlink(missing_ok=True)
    done = _read_records(log)
    if retry_errors:
        done = {qid: r for qid, r in done.items() if r["error"] is None}
    questions = [q for db in databases for q in load_questions(db) if not qids or q.qid in qids]
    todo = [q for q in questions if q.qid not in done]
    print(f"{system_name}: {len(todo)} to run, {len(questions) - len(todo)} already in {log.relative_to(paths.ROOT)}",
          flush=True)
    meta = {"system": system_name, "model": model, "endpoint": endpoint,
            "options": {k: v for k, v in options.items() if k not in ("api_key",)}}
    spend0 = _proxy_spend(endpoint)
    with Meter(endpoint) as meter:
        system = system_cls(model, meter.url, **options)
        for q in todo:
            if budget is not None and spend0 is not None:
                spent = (_proxy_spend(endpoint) or 0.0) - spend0
                if spent > budget:
                    print(f"stopping: ${spent:.2f} of new spend is over the --budget of ${budget:.2f}", flush=True)
                    break
            gold = execute(paths.require_database(q.db), q.gold_sql)
            query = load_query("aisql", q.qid)  # SWAN 2.0: one query, every system
            record = {"qid": q.qid, "db": q.db, "match": False, "quality": 0.0, "error": None}
            meter.reset()
            misses0 = meter.upstream_misses()
            start = time.time()
            try:
                predicted = system.execute(q, query)
                record["match"] = exact_match(predicted, gold, q.gold_sql, query)
                record.update(quality(predicted, gold, q.gold_sql, query))
                record["n_rows"], record["n_gold"] = len(predicted), len(gold)
                record["rows"] = [list(r) for r in predicted[:MAX_STORED_ROWS]]
            except Unsupported as ex:  # the system's dialect cannot express the question: wrong answer, counted apart
                record["error"] = f"{type(ex).__name__}: {ex}"[:1000]
                record["unsupported"] = True
            except Exception as ex:  # noqa: BLE001 -- a failing query is a wrong answer, recorded
                record["error"] = f"{type(ex).__name__}: {ex}"[:1000]
            record["seconds"] = round(time.time() - start, 2)
            record.update(meter.snapshot())
            misses1 = meter.upstream_misses()
            record["fresh_calls"] = misses1 - misses0 if misses0 is not None and misses1 is not None else None
            if hasattr(system, "last_usage"):
                record.update(system.last_usage())
            record["ran_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with open(log, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")
            verdict = (f"quality {record['quality']:.2f}" + (" (exact)" if record["match"] else "")
                       if not record["error"] else "ERROR " + record["error"][:80])
            print(f"{q.qid:24} {record['seconds']:8.1f}s calls={record['requests']:6} "
                  f"fresh={record['fresh_calls']!s:>6} ${record['cost_usd']:.4f}  {verdict}", flush=True)
        if hasattr(system, "close"):
            system.close()
    report = write_scores(run_dir, meta)
    o = report["overall"]
    unsupported = f" ({o['unsupported']} unsupported; {o['quality_supported']} on the rest)" if o.get("unsupported") else ""
    print(f"{system_name}: quality {o['quality']}{unsupported}, {o['correct']}/{o['total']} exact, {o['requests']} calls, ${o['cost_usd']}, "
          f"{o['seconds']}s -> {(run_dir / 'scores.json').relative_to(paths.ROOT)}", flush=True)
    return report
