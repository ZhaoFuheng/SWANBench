"""ThalamusDB (itrummer/thalamusdb, PyPI `thalamusdb`): SQL with `NLfilter` / `NLjoin` predicates over
DuckDB, processed approximately until its error bound is met. Runs the question's AISQL query translated
into that dialect (`translate_thalamusdb.py`), with the stop conditions lifted so the result is exact.

ThalamusDB pins its dependencies (sqlglot 27 among them) and cannot share the benchmark's environment, so
it runs in its own interpreter (`scripts/setup_thalamusdb.sh` creates `.venv-thalamusdb`; `--thalamusdb-python`
or `SWAN_THALAMUSDB_PYTHON` names another). Its DuckDB cannot open the benchmark's database files (a newer
storage format), so each masked database is copied once from its parquet export into
`data/databases/masked/<db>/thalamus.duckdb` by that interpreter. Calls, tokens and cost come from the meter
as for every system (ThalamusDB's requests are ordinary chat completions through litellm); its own call
counter is kept as `engine_llm_calls`.

Only `ai_filter` inside a WHERE clause has a ThalamusDB form; a question with `ai_classify`, `ai_complete`,
`ai_agg` or a filter in a CASE branch is recorded as unsupported (score 0, counted separately).
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from .. import paths
from ..translate_thalamusdb import to_thalamusdb
from .plop import ensure_parquet

RUNNER = Path(__file__).resolve().parent.parent / "thalamusdb_runner.py"
DEFAULT_PYTHON = paths.ROOT / ".venv-thalamusdb" / "bin" / "python"


def ensure_thalamus_db(db: str, python: str, duckdb_bin: str) -> Path:
    """A copy of the masked database that ThalamusDB's DuckDB can open, built once from the parquet export."""
    parquet = ensure_parquet(db, duckdb_bin)
    out = parquet.parent / "thalamus.duckdb"
    if out.is_file():
        return out
    tmp = out.with_suffix(".duckdb.tmp")  # built here and renamed on success, so a failed build is never reused
    # column names are made identifier-safe (ThalamusDB writes them unquoted); the translator renames the same way
    script = (
        "import duckdb, glob, os, re, sys\n"
        "out, parquet = sys.argv[1], sys.argv[2]\n"
        "con = duckdb.connect(out)\n"
        "for p in sorted(glob.glob(os.path.join(parquet, '*.parquet'))):\n"
        "    t = os.path.basename(p)[:-8]\n"
        "    cols = [r[0] for r in con.execute('DESCRIBE SELECT * FROM read_parquet(?)', [p]).fetchall()]\n"
        "    sel = ', '.join('\"%s\" AS \"%s\"' % (c.replace('\"', '\"\"'), re.sub(r'[^0-9A-Za-z_]', '_', c)) for c in cols)\n"
        "    con.execute('CREATE TABLE \"' + t.replace('\"', '\"\"') + '\" AS SELECT ' + sel + ' FROM read_parquet(?)', [p])\n"
        "con.close()\n")
    subprocess.run([python, "-c", script, str(tmp), str(parquet)], check=True, capture_output=True, text=True)
    os.replace(tmp, out)
    return out


class ThalamusDBSystem:
    name = "thalamusdb"

    def __init__(self, model: str, endpoint: str | None, concurrency: int = 20, thalamusdb_python: str | None = None,
                 duckdb_bin: str | None = None, timeout: float = 4 * 3600, **_):
        self.python = thalamusdb_python or os.environ.get("SWAN_THALAMUSDB_PYTHON") or str(DEFAULT_PYTHON)
        if not Path(self.python).is_file():
            raise SystemExit("ThalamusDB's interpreter is missing: run scripts/setup_thalamusdb.sh "
                             "(or pass --thalamusdb-python / set SWAN_THALAMUSDB_PYTHON)")
        self.duckdb_bin = duckdb_bin or os.environ.get("SWAN_AISQL_DUCKDB")
        if not self.duckdb_bin or not Path(self.duckdb_bin).is_file():
            raise SystemExit("the parquet export needs the SWAN-AISQL binary: pass --duckdb-bin (or set SWAN_AISQL_DUCKDB)")
        if endpoint is None:
            raise SystemExit("ThalamusDB has no stub mode: run it against an endpoint")
        self.model, self.concurrency, self.timeout = model, concurrency, timeout
        # litellm needs the provider prefix; the request body carries the bare model name
        lm_model = model if "/" in model else f"openai/{model}"
        call = {"model": lm_model, "api_base": endpoint.rstrip("/") + "/v1", "api_key": "sk-test", "temperature": 0,
                "drop_params": True}  # litellm's client rejects temperature=0 for gpt-5-family names; drop it instead
        self._dir = tempfile.mkdtemp(prefix="thalamusdb_")
        self.config = Path(self._dir) / "models.json"
        self.config.write_text(json.dumps({"models": [{"modalities": ["text", "image"], "priority": 10,
                                                        "kwargs": {"filter": call, "join": call}}]}, indent=1))
        self._usage: dict = {}

    def execute(self, question, query: str) -> list[tuple]:
        self._usage = {}  # an unsupported or failed question must not report the previous question's counters
        sql = to_thalamusdb(query.strip().rstrip(";"))  # raises Unsupported for what ThalamusDB cannot express
        db_file = ensure_thalamus_db(question.db, self.python, self.duckdb_bin)
        p = subprocess.run([self.python, str(RUNNER), str(db_file), str(self.config), str(self.concurrency)],
                           input=sql, text=True, capture_output=True, timeout=self.timeout, check=False)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip()[-1000:] or f"thalamusdb exited with {p.returncode}")
        out = json.loads(p.stdout)
        self._usage = {"engine_llm_calls": out["llm_calls"],
                       "engine_total_tokens": out["input_tokens"] + out["output_tokens"]}
        return [tuple(r) for r in out["rows"]]

    def last_usage(self) -> dict:
        return self._usage
