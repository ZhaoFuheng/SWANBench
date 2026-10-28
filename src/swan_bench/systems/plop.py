"""PLOP (Morrila): runs the question's AISQL query translated into its dialect (`translate_plop.py`) on the
authors' DuckDB fork, in its DP cost-model mode.

The fork is public (github.com/DMoRanSky/Morrila); this adapter needs its shell (`--plop-bin` / `SWAN_PLOP_BIN`),
built with the endpoint/model/answer-text edits in SWAN-AISQL's `aisql-bench/plop_fork.patch` (see `PLOP_FORK.md`). The fork cannot open the
benchmark's DuckDB files (a newer storage format), so each masked database is exported once to parquet
(`data/databases/masked/<db>/parquet/`) and read through views. Its calls, tokens and cost are the meter's,
measured on the fork's `/v1/responses` requests like every other system's.
"""

import os
import subprocess
import tempfile
from pathlib import Path

from .. import paths
from ..translate_plop import to_plop
from .aisql import _NUMERIC_AS_TEXT, _json_rows, _numeric, ensure_duckdb



def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def ensure_parquet(db: str, duckdb_bin: str) -> Path:
    """Export the masked database's tables to parquet once (with the SWAN-AISQL binary, which can read it)."""
    out = paths.require_database(db, masked=True).parent / "parquet"
    marker = out / ".complete"
    if marker.is_file():
        return out
    db_file = ensure_duckdb(db, duckdb_bin)
    out.mkdir(parents=True, exist_ok=True)
    tables = subprocess.run([duckdb_bin, "-readonly", "-csv", "-noheader", str(db_file)],
                            input="SELECT table_name FROM information_schema.tables WHERE table_schema = 'main';",
                            text=True, capture_output=True, check=True).stdout.split()
    script = "\n".join(f"COPY (SELECT * FROM {_quote(t)}) TO '{out / (t + '.parquet')}' (FORMAT PARQUET);" for t in tables)
    subprocess.run([duckdb_bin, "-readonly", "-bail", str(db_file)], input=script, text=True, capture_output=True, check=True)
    marker.write_text("\n".join(tables) + "\n")
    return out


class PLOPSystem:
    name = "plop"

    def __init__(self, model: str, endpoint: str | None, plop_bin: str | None = None, duckdb_bin: str | None = None,
                 concurrency: int = 20, timeout: float = 4 * 3600, mode: str = "costmodel",
                 api_key: str | None = None, **_):
        self.plop_bin = plop_bin or os.environ.get("SWAN_PLOP_BIN")
        if not self.plop_bin or not Path(self.plop_bin).is_file():
            raise SystemExit("pass --plop-bin (or set SWAN_PLOP_BIN) to the Morrila fork's duckdb shell")
        self.duckdb_bin = duckdb_bin or os.environ.get("SWAN_AISQL_DUCKDB")
        if not self.duckdb_bin or not Path(self.duckdb_bin).is_file():
            raise SystemExit("the parquet export needs the SWAN-AISQL binary: pass --duckdb-bin (or set SWAN_AISQL_DUCKDB)")
        if endpoint is None:
            raise SystemExit("PLOP has no stub mode: run it against an endpoint")
        self.model, self.endpoint, self.timeout = model, endpoint, timeout
        self.env = {**os.environ, "SEMANTIC_OPTIMIZER": "true", "DUCKDB_SEMANTIC_MODE": mode,
                    "LLM_API_URL": endpoint.rstrip("/") + "/v1/responses", "API_KEY": api_key or "sk-test",
                    "LLM_MODEL": model, "NUM_LLM_WORKERS": str(concurrency)}

    def execute(self, question, query: str) -> list[tuple]:
        parquet = ensure_parquet(question.db, self.duckdb_bin)
        views = "\n".join(f"CREATE VIEW {_quote(p.stem)} AS SELECT * FROM read_parquet('{p}');"
                          for p in sorted(parquet.glob("*.parquet")))
        sql = to_plop(query.strip().rstrip(";"))
        with tempfile.TemporaryDirectory() as tmp:
            out, desc = Path(tmp) / "out.json", Path(tmp) / "describe.json"
            # DESCRIBE binds the query without running it: the column types tell which numbers arrive as text
            script = f"{views}\n.mode json\n.once {desc}\nDESCRIBE {sql};\n.once {out}\n{sql};\n"
            p = subprocess.run([self.plop_bin, "-bail"], input=script, text=True, capture_output=True,
                               timeout=self.timeout, env=self.env, cwd=tmp, check=False)  # exit code handled below
            rows = _json_rows(out)
            types = [r[1] for r in _json_rows(desc)]
            numeric = [i for i, t in enumerate(types) if t.split("(")[0] in _NUMERIC_AS_TEXT]
            if numeric:
                rows = [tuple(_numeric(v) if i in numeric else v for i, v in enumerate(r)) for r in rows]
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip()[-1000:] or f"plop exited with {p.returncode}")
        return rows

    def last_usage(self) -> dict:
        return {}  # nothing beyond the meter's figures
