"""The system adapters: type mapping, result conversion, and (slow) every query against a stub model."""

import os

import pytest

from swan_bench import paths
from swan_bench.data import load_query, load_questions
from swan_bench.systems import aisql as aisql_adapter
from swan_bench.systems.aisql import _json_rows, duckdb_type, ensure_duckdb
from swan_bench.systems.blendsql import _zero_shot
from swan_bench.lotus_exec import rows_of


def test_sqlite_affinity_maps_to_duckdb_types():
    assert duckdb_type("INTEGER") == duckdb_type("bigint") == "BIGINT"
    assert duckdb_type("REAL") == duckdb_type("NUMERIC(10,2)") == duckdb_type("float") == "DOUBLE"
    assert duckdb_type("TEXT") == duckdb_type("DATE") == duckdb_type("") == "VARCHAR"


def test_duplicate_column_names_survive_json(tmp_path):
    out = tmp_path / "out.json"
    out.write_text('[{"name": "a", "name": "b", "n": 1}]')
    assert _json_rows(out) == [("a", "b", 1)]
    (tmp_path / "empty.json").write_text("")
    assert _json_rows(tmp_path / "empty.json") == []


def _masked_or_skip(db):
    if not paths.database_path(db, masked=True).is_file():
        pytest.skip("run `swan-bench prepare` first")


def test_an_interrupted_conversion_leaves_no_database(tmp_path, monkeypatch):
    _masked_or_skip("superhero")
    target = tmp_path / "superhero.duckdb"

    def interrupted(args, **kwargs):  # the shell had started writing its file (the last argument) when Ctrl-C came
        open(args[-1], "w").write("half a database")
        raise KeyboardInterrupt

    monkeypatch.setattr(aisql_adapter.subprocess, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        ensure_duckdb("superhero", "duckdb", target=target)
    assert list(tmp_path.glob("superhero.duckdb*")) == []  # nothing a later run could take as converted


def test_a_failed_conversion_leaves_no_database(tmp_path):
    _masked_or_skip("superhero")
    target = tmp_path / "superhero.duckdb"
    shell = tmp_path / "shell"  # a stand-in for a DuckDB shell that dies after it started writing its file
    shell.write_text('#!/bin/sh\nfor last; do :; done\necho partial > "$last"\nexit 1\n')
    shell.chmod(0o755)
    with pytest.raises(RuntimeError, match="failed"):
        ensure_duckdb("superhero", str(shell), target=target)
    assert list(tmp_path.glob("superhero.duckdb*")) == []
    shell.write_text('#!/bin/sh\nfor last; do :; done\ncase " $* " in *" -bail "*) echo incomplete > "$last";; esac\nexit 0\n')
    with pytest.raises(RuntimeError, match="lost rows"):  # it exits cleanly, but the tables are not in the file
        ensure_duckdb("superhero", str(shell), target=target)
    assert list(tmp_path.glob("superhero.duckdb*")) == []


def test_lotus_results_become_plain_rows():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"a": [1, 2], "b": [float("nan"), 0.5]})
    assert rows_of(df) == [(1, None), (2, 0.5)]
    assert rows_of(df["a"]) == [(1,), (2,)]
    assert rows_of([(1, "x")]) == [(1, "x")]
    assert rows_of(3) == [(3,)]


def _run_stub(system, question, **options):
    from swan_bench.meter import Meter
    from swan_bench.systems import load_system

    with Meter(None) as meter:
        return load_system(system)("stub", meter.url, **options).execute(question, load_query("aisql", question.qid))


@pytest.mark.slow
@pytest.mark.parametrize("question", load_questions(), ids=lambda q: q.qid)
def test_lotus_query_runs_on_masked_database(question):
    pytest.importorskip("lotus")
    if not paths.database_path(question.db, masked=True).is_file():
        pytest.skip("run `swan-bench prepare` first")
    assert isinstance(_run_stub("lotus", question), list)


@pytest.mark.slow
@pytest.mark.parametrize("question", load_questions(), ids=lambda q: q.qid)
def test_aisql_query_runs_on_masked_database(question):
    duckdb_bin = os.environ.get("SWAN_AISQL_DUCKDB")
    if not duckdb_bin:
        pytest.skip("set SWAN_AISQL_DUCKDB to the SWAN-AISQL duckdb binary")
    if not paths.database_path(question.db, masked=True).is_file():
        pytest.skip("run `swan-bench prepare` first")
    assert isinstance(_run_stub("aisql", question, duckdb_bin=duckdb_bin, mock=True), list)


def test_numeric_text_becomes_a_number():
    from swan_bench.systems.aisql import _numeric
    assert _numeric("3") == 3 and _numeric("1.5") == 1.5 and _numeric(None) is None and _numeric(2) == 2


def test_blendsql_prompt_loses_its_one_shot_example():
    prompt = ("You are a helpful assistant. Output 'True' or 'False'. An example is shown below.\n\n"
              "QUESTION:\nIs this city in the California Bay Area?\n\nCONTEXT:\n{\"city\": \"San Jose\"}\n\n"
              "ANSWER:\nTrue\n\n---\n\nQUESTION:\nIs this player tall?\n\nCONTEXT:\n{\"name\": \"Ross\"}\n\nANSWER:\n")
    assert _zero_shot(prompt) == ("You are a helpful assistant. Output 'True' or 'False'.\n\n"
                                  "QUESTION:\nIs this player tall?\n\nCONTEXT:\n{\"name\": \"Ross\"}\n\nANSWER:\n")
    assert _zero_shot("no example here") == "no example here"
