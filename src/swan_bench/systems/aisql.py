"""SWAN-AISQL: AI functions in DuckDB SQL (`ai_filter`, `ai_classify`, `ai_complete`, ...), `queries/aisql/<qid>.sql`.

SWAN-AISQL is a DuckDB build with the `aisql` extension linked in, so it needs no Python package; pass the
binary with `--duckdb-bin` (or `SWAN_AISQL_DUCKDB`). It reads DuckDB files, so the first run converts each
masked sqlite database to `data/databases/masked/<db>/<db>.duckdb`, with the same binary so the storage
version always matches. Every query runs in a fresh CLI process with JSON output.
"""

import csv
import json
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from .. import paths

NULL = "\\N"


def duckdb_type(declared: str) -> str:
    """The DuckDB column type for a sqlite declared type, following sqlite's affinity rules."""
    d = (declared or "").upper()
    if "INT" in d:
        return "BIGINT"
    if any(t in d for t in ("REAL", "FLOA", "DOUB", "NUMERIC", "DECIMAL")):
        return "DOUBLE"
    return "VARCHAR"  # TEXT, DATE, DATETIME and untyped columns stay text, as sqlite stores them


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def duckdb_path(db: str) -> Path:
    return paths.MASKED_DIR / db / f"{db}.duckdb"


#: DuckDB 2.0's shell turns on an "agent mode" (output truncated to 1,000 rows / 10,000 bytes) when the environment
#: carries an AI coding agent's marker (AI_AGENT, AGENT, ...) and stdout is a pipe: every CLI we spawn pins it off.
_NO_AGENT_ENV = {**os.environ, "DUCKDB_AGENT_MODE": "0"}

def ensure_duckdb(db: str, duckdb_bin: str, force: bool = False) -> Path:
    target = duckdb_path(db)
    source = paths.require_database(db, masked=True)
    if target.is_file() and not force and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    target.unlink(missing_ok=True)
    con = sqlite3.connect(source)
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    counts = {}
    with tempfile.TemporaryDirectory() as tmp:
        script = []
        for table in tables:
            columns = [(r[1], r[2]) for r in con.execute(f"PRAGMA table_info({_quote(table)})")]
            file = Path(tmp) / f"{len(script)}.csv"
            with open(file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([c for c, _ in columns])
                n = 0
                for row in con.execute(f"SELECT * FROM {_quote(table)}"):
                    writer.writerow([NULL if v is None else v for v in row])
                    n += 1
            counts[table] = n
            cols = ", ".join(f"{_quote(c)} {duckdb_type(t)}" for c, t in columns)
            script.append(f"CREATE TABLE {_quote(table)} ({cols});\n"
                          f"COPY {_quote(table)} FROM '{file}' (HEADER, NULL '{NULL}', QUOTE '\"', ESCAPE '\"');")
        p = subprocess.run([duckdb_bin, "-bail", str(target)], input="\n".join(script), text=True, env=_NO_AGENT_ENV,
                           capture_output=True)
        if p.returncode != 0:
            target.unlink(missing_ok=True)
            raise RuntimeError(f"converting {db} to DuckDB failed: {p.stderr.strip()[-500:]}")
    con.close()
    check = "\n".join(f"SELECT '{t}', count(*) FROM {_quote(t)};" for t in tables)
    p = subprocess.run([duckdb_bin, "-readonly", "-csv", "-noheader", str(target)], input=check, text=True, env=_NO_AGENT_ENV,
                       capture_output=True)
    got = {line.split(",")[0]: int(line.split(",")[1]) for line in p.stdout.split()}
    if got != counts:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"converting {db} to DuckDB lost rows: {counts} vs {got}")
    return target


#: JSON output writes these types as strings; they are numbers
_NUMERIC_AS_TEXT = ("HUGEINT", "UHUGEINT", "DECIMAL")


def _numeric(value):
    if not isinstance(value, str):
        return value
    try:
        return int(value)
    except ValueError:
        return float(value)


def _json_rows(path: Path) -> list[tuple]:
    text = path.read_text().strip() if path.is_file() else ""
    if not text:
        return []
    # object_pairs_hook keeps every column, even when two share a name
    return [tuple(v for _, v in pairs) for pairs in json.loads(text, object_pairs_hook=list)]


class AISQLSystem:
    name = "aisql"

    def __init__(self, model: str, endpoint: str | None, duckdb_bin: str | None = None, mock: bool = False,
                 timeout: float = 4 * 3600, settings: dict | None = None, api_key: str | None = None, **_):
        self.duckdb_bin = duckdb_bin or os.environ.get("SWAN_AISQL_DUCKDB")
        if not self.duckdb_bin or not Path(self.duckdb_bin).is_file():
            raise SystemExit("pass --duckdb-bin (or set SWAN_AISQL_DUCKDB) to the SWAN-AISQL duckdb binary")
        self.model, self.endpoint, self.mock, self.timeout = model, endpoint, mock, timeout
        self.settings = settings or {}
        self.api_key = api_key  # sent as a Bearer header, which the meter forwards to the endpoint
        self._usage: dict = {}

    def _preamble(self) -> str:
        if self.mock:
            lines = ["CALL ai_mock_start();"]
        else:
            lines = [f"SET ai_endpoint = '{self.endpoint}';", f"SET ai_model = '{self.model}';"]
            if self.api_key:
                lines.append(f"SET ai_api_key = '{self.api_key}';")
        lines += [f"SET {k} = '{v}';" for k, v in self.settings.items()]
        return "\n".join(lines)

    def execute(self, question, query: str) -> list[tuple]:
        db_file = ensure_duckdb(question.db, self.duckdb_bin)
        with tempfile.TemporaryDirectory() as tmp:
            out, usage, desc = Path(tmp) / "out.json", Path(tmp) / "usage.json", Path(tmp) / "describe.json"
            sql = query.strip().rstrip(";")
            # DESCRIBE only binds the query (no AI call); it gives the column types for _NUMERIC_AS_TEXT
            script = (f"{self._preamble()}\n.mode json\n.once {desc}\nDESCRIBE {sql};\n.once {out}\n{sql};\n"
                      f".once {usage}\n"
                      "SELECT (sum(llm_calls) - sum(embed_calls))::BIGINT AS llm_calls, "
                      "sum(cache_hits)::BIGINT AS cache_hits, sum(failed_calls)::BIGINT AS failed_calls, "
                      "sum(total_tokens)::BIGINT AS total_tokens, sum(cost_usd) AS cost_usd FROM ai_usage();\n")
            # DuckDB 2.0's shell turns on an "agent mode" (output truncated to 1,000 rows / 10,000 bytes) when the
            # environment carries an AI coding agent's marker and stdout is a pipe: pin it off
            p = subprocess.run([self.duckdb_bin, "-readonly", "-bail", str(db_file)], input=script, text=True,
                               capture_output=True, timeout=self.timeout, env=_NO_AGENT_ENV)
            rows = _json_rows(out)
            types = [r[1] for r in _json_rows(desc)]
            numeric = [i for i, t in enumerate(types) if t.split("(")[0] in _NUMERIC_AS_TEXT]
            if numeric:
                rows = [tuple(_numeric(v) if i in numeric else v for i, v in enumerate(r)) for r in rows]
            usage_rows = json.loads(usage.read_text() or "[{}]") if usage.is_file() else [{}]
        self._usage = {"engine_" + k: v for k, v in (usage_rows[0] if usage_rows else {}).items()}
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip()[-1000:] or f"duckdb exited with {p.returncode}")
        return rows

    def last_usage(self) -> dict:
        return self._usage
