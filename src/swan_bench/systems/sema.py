"""Sema (VLDB 2026): runs the question's AISQL query translated into its dialect (`translate_sema.py`) on the
authors' DuckDB fork, with its own settings (a lone filter or map asks one row per request).

The fork is public (github.com/BITQiKangK/Sema); this adapter needs its shell (`--sema-bin` / `SWAN_SEMA_BIN`),
built by SWAN-AISQL's `aisql-bench/setup_sema.sh` (pinned commit, `sema_fork.patch`). The fork cannot open the
benchmark's DuckDB files (a newer storage format), so each masked database is converted once with the fork's own
shell, through the same typed CSV import as SWAN-AISQL's copy (`<db>.sema.duckdb`), so both see the same column
types. Sema calls the OpenAI-compatible endpoint the runner hands it (the meter) through its `llm_url` setting,
with its own prompts; its calls, tokens and cost are the meter's, like every other system's.

Concurrency is the runner's (20 by default): `sema_fork.patch` reads SEMA_LLM_MAX_CONNECTIONS (requests in flight
per thread) and SEMA_LLM_TIMEOUT_S (Sema's 30 s request timeout runs from the moment a request is queued, so with a
cap the queued tail of a 2,048-row chunk would time out and be retried), and every query runs with SET threads=1
so the cap holds for the process. Every Sema setting stays at its default (semantic_batch_size 1).
"""

import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from .. import paths
from ..translate_sema import to_sema
from .aisql import _NUMERIC_AS_TEXT, _json_rows, _numeric, ensure_duckdb


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sema_duckdb_path(db: str) -> Path:
    return paths.MASKED_DIR / db / f"{db}.sema.duckdb"


class SemaSystem:
    name = "sema"
    #: Sema turns a request that failed into false (a filter) or an empty string (a map) and still exits 0, so the
    #: harness records an error for a question during which the meter saw a model request fail
    answers_failed_calls = True

    def __init__(self, model: str, endpoint: str | None, sema_bin: str | None = None, concurrency: int = 20,
                 timeout: float = 4 * 3600, api_key: str | None = None, **_):
        self.sema_bin = sema_bin or os.environ.get("SWAN_SEMA_BIN")
        if not self.sema_bin or not Path(self.sema_bin).is_file():
            raise SystemExit("pass --sema-bin (or set SWAN_SEMA_BIN) to Sema's duckdb shell (aisql-bench/setup_sema.sh)")
        self.sema_bin = str(Path(self.sema_bin).resolve())  # each query runs in its own temporary directory
        self.model, self.endpoint, self.timeout = model, endpoint.rstrip("/"), timeout
        self.api_key = "sk-test"  # Sema only talks to the local meter: no provider key goes into its requests
        self.env = {**os.environ, "DUCKDB_AGENT_MODE": "0", "SEMA_LLM_MAX_CONNECTIONS": str(concurrency),
                    "SEMA_LLM_TIMEOUT_S": "1800"}

    @staticmethod
    def _labels(db: str):
        source = paths.require_database(db, masked=True)

        def resolve(table: str, column: str) -> list[str]:
            con = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
            try:
                return [str(r[0]) for r in con.execute(
                    f"SELECT DISTINCT {_quote(column)} FROM {_quote(table)} WHERE {_quote(column)} IS NOT NULL ORDER BY 1")]
            finally:
                con.close()
        return resolve

    def execute(self, question, query: str) -> list[tuple]:
        db_file = ensure_duckdb(question.db, self.sema_bin, target=sema_duckdb_path(question.db))
        sql = to_sema(query.strip().rstrip(";"), self._labels(question.db))
        model = self.model.replace("'", "''")
        settings = (f"SET llm_url='{self.endpoint}/v1/chat/completions';\nSET llm_model='{model}';\n"
                    f"SET llm_api_key='{self.api_key}';\nSET threads=1;\n")
        with tempfile.TemporaryDirectory() as tmp:
            out, desc = Path(tmp) / "out.json", Path(tmp) / "describe.json"
            # DESCRIBE binds the query without running it (Sema calls the model only while executing): the column
            # types tell which numbers arrive as text
            script = f"{settings}.mode json\n.once {desc}\nDESCRIBE {sql};\n.once {out}\n{sql};\n"
            p = subprocess.run([self.sema_bin, "-init", os.devnull, "-readonly", "-bail", str(db_file)], input=script, text=True,
                               capture_output=True, timeout=self.timeout, env=self.env, cwd=tmp, check=False)
            rows = _json_rows(out)
            types = [r[1] for r in _json_rows(desc)]
            numeric = [i for i, t in enumerate(types) if t.split("(")[0] in _NUMERIC_AS_TEXT]
            if numeric:
                rows = [tuple(_numeric(v) if i in numeric else v for i, v in enumerate(r)) for r in rows]
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip()[-1000:] or f"sema exited with {p.returncode}")
        return rows

    def last_usage(self) -> dict:
        return {}  # nothing beyond the meter's figures
