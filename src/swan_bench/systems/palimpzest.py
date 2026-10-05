"""Palimpzest (MIT DSG; PyPI `palimpzest`) with its Abacus optimizer. Runs the question's AISQL query the
way `lotus_exec.py` runs it for LOTUS, in the query's written order, with every semantic operator handed to
Palimpzest: one `sem_filter` / `sem_add_columns` / `sem_agg` program per AI call over the rows it reaches,
whose physical plan Abacus chooses (`optimizer_strategy="pareto"`) with its default cost model: Palimpzest
runs its sample-based cost estimation only when given a validator or training set, which the benchmark
does not supply, so no sampling calls are made. The relational parts run in sqlite, as for LOTUS and
BlendSQL.

Palimpzest pulls in torch and cannot share the benchmark's environment, so it runs in its own interpreter
(`scripts/setup_palimpzest.sh` creates `.venv-palimpzest`; `--palimpzest-python` / `SWAN_PALIMPZEST_PYTHON`
names another), as one long-lived server process per run (`palimpzest_ops_server.py`, JSON lines over pipes), so
its import time is paid once and not per question. Its calls go through the meter like every system's: it
treats the benchmark model as a self-hosted model and prices it at zero, so the meter's cost is the one used.

Translation: `ai_filter` -> sem_filter(question, depends_on=[context column]); `ai_complete` ->
sem_add_columns of a str column described by the question and SWAN's answer-format suffix; `ai_classify`
-> the same with "Answer with exactly one of: <labels>."; `ai_agg` -> sem_agg. Palimpzest shows the model
the context value under its column name, its own form of the `name: value` context the other systems show.
"""

import json
import os
import subprocess
from pathlib import Path

from .. import paths
from ..aisql import AICall

SERVER = Path(__file__).resolve().parent.parent / "palimpzest_ops_server.py"
DEFAULT_PYTHON = paths.ROOT / ".venv-palimpzest" / "bin" / "python"


class PalimpzestOps:
    """The LOTUS-executor operator interface (`filter` / `complete` / `classify` / `agg`), served by Palimpzest."""

    def __init__(self, python: str, model: str, endpoint: str, concurrency: int, optimizer: str, policy: str):
        paths.RUNS.mkdir(parents=True, exist_ok=True)
        self.log = paths.RUNS / "palimpzest_server.stderr"  # Palimpzest's own output; read back if the server dies
        self.proc = subprocess.Popen([python, str(SERVER), model, endpoint, str(concurrency), "--optimizer", optimizer,
                                      "--policy", policy], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=open(self.log, "a"), text=True, bufsize=1)  # noqa: SIM115 -- closed with the process

    def _ask(self, req: dict) -> list:
        self.proc.stdin.write(json.dumps(req, default=str) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:  # the server died: no later question can run, so stop the run with its last output
            tail = self.log.read_text()[-2000:] if self.log.exists() else ""
            raise SystemExit(f"the Palimpzest server exited (code {self.proc.poll()}); its stderr ends with:\n{tail}")
        resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return resp["answers"]

    def filter(self, call: AICall, values: list) -> list:
        return [bool(v) for v in self._ask({"op": "filter", "name": call.name, "question": call.question, "values": values})]

    def complete(self, call: AICall, values: list) -> list:
        return self._ask({"op": "complete", "name": call.name, "question": call.question, "suffix": call.suffix, "values": values})

    def classify(self, call: AICall, values: list, labels: list[str]) -> list:
        return self._ask({"op": "classify", "name": call.name, "question": call.question, "labels": labels, "values": values})

    def agg(self, call: AICall, values: list) -> str:
        return self._ask({"op": "agg", "name": call.name, "instruction": call.instruction, "values": values})[0]

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=30)
        except Exception:  # noqa: BLE001
            self.proc.kill()
        finally:
            if self.proc.stderr is not None:
                self.proc.stderr.close()


class PalimpzestSystem:
    name = "palimpzest"

    def __init__(self, model: str, endpoint: str | None, concurrency: int = 20, palimpzest_python: str | None = None,
                 optimizer: str = "pareto", policy: str = "MaxQuality", **_):
        python = palimpzest_python or os.environ.get("SWAN_PALIMPZEST_PYTHON") or str(DEFAULT_PYTHON)
        if not Path(python).is_file():
            raise SystemExit("Palimpzest's interpreter is missing: run scripts/setup_palimpzest.sh "
                             "(or pass --palimpzest-python / set SWAN_PALIMPZEST_PYTHON)")
        if endpoint is None:
            raise SystemExit("Palimpzest has no stub mode: run it against an endpoint")
        self.ops = PalimpzestOps(python, model, endpoint, concurrency, optimizer, policy)

    def execute(self, question, query: str) -> list[tuple]:
        from ..lotus_exec import LotusExecutor, rows_of

        executor = LotusExecutor(paths.require_database(question.db, masked=True), ops=self.ops)
        try:
            return rows_of(executor.run(query))
        finally:
            executor.close()

    def close(self) -> None:
        self.ops.close()
