"""Run one query on ThalamusDB and print the result as JSON. Runs in ThalamusDB's own interpreter
(`.venv-thalamusdb`, see scripts/setup_thalamusdb.sh), so it imports nothing from swan_bench.

    python thalamusdb_runner.py DB_FILE MODELS_JSON DOP < query.sql

DB_FILE is a DuckDB file ThalamusDB's DuckDB can open (column names identifier-safe, see the system
adapter); MODELS_JSON its model configuration; DOP the number of requests in flight.

ThalamusDB (0.1.x) evaluates `NLfilter` over a base table named by its alias and knows no CTEs, so the
runner prepares the query for it, without changing what is asked:
- a table used under another name (`superhero AS T1`) is copied into a temporary table called `T1`;
- a CTE without semantic predicates is materialised as a temporary table;
- a CTE with semantic predicates is run through ThalamusDB first and its result materialised, so every
  semantic predicate is evaluated exactly once, in order;
- the remaining query runs through ThalamusDB (or plain DuckDB when it has no semantic predicate left).
ThalamusDB's stop conditions (default: 1,000 calls, 600 s) are lifted so that it processes every row and
its result is exact (its own `max_error = 0`); a `LIMIT k`, which ThalamusDB strips to drive its progress
loop, is applied to the rows it returns. ThalamusDB's progress output goes to stderr; stdout carries one
JSON object: {"columns", "rows", "llm_calls", "input_tokens", "output_tokens", "rows_per_filter"}.
"""
import json
import math
import sys

import sqlglot
from sqlglot import exp


def _py(value):
    if hasattr(value, "item"):  # numpy scalar
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    if value is not None and type(value).__name__ in ("Timestamp", "NaTType", "Timedelta", "date", "datetime"):
        return None if str(value) == "NaT" else str(value)
    return value


def _has_nl(node: exp.Expression) -> bool:
    return any(isinstance(f, exp.Anonymous) and f.name.lower() in ("nlfilter", "nljoin") for f in node.find_all(exp.Anonymous))


class Runner:
    def __init__(self, db_path, config_path, dop):
        from tdb.data.relational import Database
        from tdb.execution.constraints import Constraints
        from tdb.execution.engine import ExecutionEngine

        self.db = Database(db_path)
        self.engine = ExecutionEngine(self.db, dop, config_path)
        self.constraints = Constraints(max_seconds=10**9, max_calls=10**12, max_tokens=10**15, max_error=0.0)
        self.calls = self.in_tok = self.out_tok = 0
        self.rows_per_filter: list[int] = []
        self.predicates: list[str] = []
        self.copied: dict[str, str] = {}  # alias -> the table it was copied from

    def sql(self, node: exp.Expression) -> str:
        return node.sql(dialect="duckdb")

    def normalize_aliases(self, root: exp.Expression) -> None:
        """`tbl AS alias` -> a temporary table named `alias`, referenced by that name (ThalamusDB fills its
        working table from the table name but keeps the alias in the predicates it copies)."""
        for table in list(root.find_all(exp.Table)):
            alias = table.alias
            if alias and alias.lower() != table.name.lower():
                seen = self.copied.get(alias.lower())
                if seen is not None and seen != table.name.lower():
                    raise ValueError(f"alias {alias!r} names both {seen!r} and {table.name!r}")
                if seen is None:
                    self.db.execute2list(f'CREATE TEMPORARY TABLE "{alias}" AS SELECT * FROM "{table.name}"')
                    self.copied[alias.lower()] = table.name.lower()
                table.set("this", exp.to_identifier(alias))
                table.set("alias", None)

    def run_semantic(self, sql: str):
        """One ThalamusDB execution; returns a DataFrame."""
        from tdb.queries.query import Query

        query = Query(self.db, sql)
        if not query.semantic_predicates:
            return self.db.execute2df(sql)
        self.predicates += [p.sql for p in query.semantic_predicates]
        df, counters = self.engine.run(query, self.constraints)
        self.calls += counters.total_LLM_calls()
        self.in_tok += counters.total_input_tokens()
        self.out_tok += counters.total_output_tokens()
        self.rows_per_filter.append(counters.processed_tasks)
        if query.limit != float("inf") and len(df) > query.limit:
            df = df.head(query.limit)
        return df

    def run(self, sql: str):
        ast = sqlglot.parse_one(sql, read="duckdb")
        with_ = ast.args.get("with")
        if with_ is not None:
            for cte in with_.expressions:
                name, body = cte.alias, cte.this
                self.normalize_aliases(body)  # stage by stage: an alias may name a CTE materialised just before
                if _has_nl(body):
                    df = self.run_semantic(self.sql(body))
                    self.db.con.register("_tdb_stage", df)
                    self.db.execute2list(f'CREATE TEMPORARY TABLE "{name}" AS SELECT * FROM _tdb_stage')
                    self.db.con.unregister("_tdb_stage")
                else:
                    self.db.execute2list(f'CREATE TEMPORARY TABLE "{name}" AS {self.sql(body)}')
            ast.set("with", None)
        self.normalize_aliases(ast)
        main_sql = self.sql(ast)
        if _has_nl(ast):
            return self.run_semantic(main_sql)
        return self.db.execute2df(main_sql)


def main() -> None:
    db_path, config_path, dop = sys.argv[1], sys.argv[2], int(sys.argv[3])
    sql = sys.stdin.read().strip().rstrip(";")
    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # ThalamusDB prints progress tables; keep stdout for the JSON result

    runner = Runner(db_path, config_path, dop)
    df = runner.run(sql)
    out = {"llm_calls": runner.calls, "input_tokens": runner.in_tok, "output_tokens": runner.out_tok,
           "rows_per_filter": runner.rows_per_filter, "predicates": runner.predicates}
    if df is not None:
        out["columns"] = [str(c) for c in df.columns]
        out["rows"] = [[_py(v) for v in row] for row in df.itertuples(index=False, name=None)]
    json.dump(out, real_stdout, default=str)
    real_stdout.write("\n")


if __name__ == "__main__":
    main()
