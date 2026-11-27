"""The Sema translation keeps every prompt and maps the AI functions onto Sema's s'...' expressions, by position;
the adapter runs Sema at its default settings."""

import inspect
import re

import pytest

from swan_bench import paths
from swan_bench.aisql import Unsupported
from swan_bench.data import load_query, load_questions
from swan_bench.systems import sema
from swan_bench.translate_sema import to_sema

FILTER = "ai_filter('Context:\n[name]: «' || t.name || '»\n\n\nClaim: Is it big? name')"
CLASSIFY = "ai_classify('Which colour? name: ' || t.name, ['red', 'blue'])"
MAP = "trim(s'Which colour? {t.name} Answer with exactly one of: red, blue.')"
YES_NO = "ai_classify('Is it big? name: ' || t.name, ['Yes', 'No'])"


def test_filter_in_where_is_a_semantic_filter():
    assert to_sema(f"SELECT t.name FROM t WHERE {FILTER}") == "SELECT t.name FROM t WHERE s'Is it big? {t.name}'"


def test_filter_outside_where_is_a_map_asked_for_true_or_false():
    out = to_sema(f"SELECT CASE WHEN {FILTER} THEN 1 ELSE 0 END FROM t")
    assert "(lower(trim(s'Is it big? {t.name} Answer with true or false only.')) = 'true')" in out


def test_yes_no_classify_in_where_is_the_question_as_a_filter():
    assert to_sema(f"SELECT t.name FROM t WHERE {YES_NO} = 'Yes'") == "SELECT t.name FROM t WHERE s'Is it big? {t.name}'"
    assert to_sema(f"SELECT t.name FROM t WHERE {YES_NO} = 'No'") == "SELECT t.name FROM t WHERE (NOT s'Is it big? {t.name}')"
    assert to_sema(f"SELECT t.name FROM t WHERE 'No' = {YES_NO}") == "SELECT t.name FROM t WHERE (NOT s'Is it big? {t.name}')"


def test_other_classify_in_where_stays_a_map_computed_in_the_from_clause():
    assert to_sema(f"SELECT t.name FROM t WHERE {CLASSIFY} = 'red'") == (
        f"SELECT t.name FROM (SELECT t.*, {MAP} AS sema_label_0 FROM t) AS t WHERE t.sema_label_0 = 'red'")
    # the plain conditions on that table alone run below the map; the join, the other AI condition and a condition
    # that also reads another table stay where they were
    out = to_sema("SELECT DISTINCT t.name FROM big AS t INNER JOIN u ON u.id = t.id "
                  f"WHERE t.size > 3 AND u.kind = 'x' AND {CLASSIFY} = 'red' AND {FILTER} AND t.size < u.size LIMIT 5")
    assert out == (f"SELECT DISTINCT t.name FROM (SELECT t.*, {MAP} AS sema_label_0 FROM big AS t WHERE t.size > 3) AS t "
                   "INNER JOIN u ON u.id = t.id WHERE u.kind = 'x' AND t.sema_label_0 = 'red' "
                   "AND s'Is it big? {t.name}' AND t.size < u.size LIMIT 5")


def test_the_rewritten_where_keeps_its_groups():
    # (a OR b) is one condition of the WHERE: without its parentheses a row of another label with t.x = 2 would pass
    out = to_sema(f"SELECT t.name FROM t INNER JOIN u ON u.id = t.id WHERE {CLASSIFY} = 'red' AND (u.x = 1 OR t.x = 2)")
    assert out == (f"SELECT t.name FROM (SELECT t.*, {MAP} AS sema_label_0 FROM t) AS t INNER JOIN u ON u.id = t.id "
                   "WHERE t.sema_label_0 = 'red' AND (u.x = 1 OR t.x = 2)")
    # a group on the classify's own table moves below the map as a group; one with an AI call stays where it is
    out = to_sema("SELECT t.name FROM t INNER JOIN u ON u.id = t.id WHERE (u.x = 1 OR t.x = 2) AND "
                  f"({CLASSIFY} = 'red') AND t.y > 0 AND (t.x = 1 OR t.y = 2) AND NOT (u.a = 1 OR u.b = 2) "
                  f"AND (t.z = 3 OR {FILTER})")
    assert out == (f"SELECT t.name FROM (SELECT t.*, {MAP} AS sema_label_0 FROM t WHERE t.y > 0 AND (t.x = 1 OR t.y = 2)) "
                   "AS t INNER JOIN u ON u.id = t.id WHERE (u.x = 1 OR t.x = 2) AND t.sema_label_0 = 'red' AND "
                   "NOT (u.a = 1 OR u.b = 2) AND (t.z = 3 OR s'Is it big? {t.name}')")


def test_classify_outside_where_is_a_trimmed_map_with_resolved_labels():
    out = to_sema("SELECT ai_classify('Which colour? name: ' || t.name, (SELECT list(DISTINCT c ORDER BY c) FROM u)) FROM t",
                  lambda table, column: ["green", "red"] if (table, column) == ("u", "c") else [])
    assert out == "SELECT trim(s'Which colour? {t.name} Answer with exactly one of: green, red.') FROM t"


def test_complete_and_agg():
    out = to_sema("SELECT ai_complete('How old is it? name: ' || t.name || ' Answer with the number only.') FROM t")
    assert out == "SELECT s'How old is it? {t.name} Answer with the number only.' FROM t"
    # sem_agg takes its instruction as plain text: nothing in it is a column reference
    out = to_sema(f"SELECT ai_agg(list(t.name), 'Summarize the group''s names.') FROM t WHERE {FILTER}")
    assert out == "SELECT sem_agg('Summarize the group''s names.', t.name) FROM t WHERE s'Is it big? {t.name}'"


def test_an_apostrophe_is_written_as_the_typographic_one_and_a_label_is_returned_as_the_database_spells_it():
    # Sema keeps '' inside s'...' as two characters, so a straight apostrophe cannot be sent
    out = to_sema("SELECT ai_complete('What is the hero''s home? name: ' || t.name) FROM t")
    assert out == "SELECT s'What is the hero’s home? {t.name}' FROM t"
    out = to_sema("SELECT ai_classify('Which race? name: ' || t.name, ['Human', 'Yoda''s species']) FROM t")
    assert out == ("SELECT replace(trim(s'Which race? {t.name} Answer with exactly one of: Human, Yoda’s species.'), "
                   "'’', '''') FROM t")
    with pytest.raises(Exception, match="brace"):
        to_sema("SELECT ai_complete('What is {this}? name: ' || t.name) FROM t")


def test_a_semantic_string_is_typed_by_the_clause_of_its_own_query():
    # in the WHERE of a subquery: a filter; in the select list of a subquery that a WHERE reads: a map
    out = to_sema(f"SELECT u.id FROM u WHERE u.id IN (SELECT t.id FROM t WHERE {FILTER})")
    assert out == "SELECT u.id FROM u WHERE u.id IN (SELECT t.id FROM t WHERE s'Is it big? {t.name}')"
    out = to_sema("SELECT u.id FROM u WHERE u.colour IN (SELECT " + CLASSIFY + " FROM t)")
    assert out == f"SELECT u.id FROM u WHERE u.colour IN (SELECT {MAP} FROM t)"


@pytest.mark.parametrize("query", [
    f"SELECT 1 FROM t JOIN u ON {FILTER}",
    f"SELECT t.g FROM t GROUP BY t.g HAVING bool_or({FILTER})",
    f"SELECT count(*) FILTER (WHERE {FILTER}) FROM t",
    "SELECT 1 FROM t WHERE ai_complete('How old is it? name: ' || t.name || ' Answer with the number only.') = '3'",
    f"SELECT 1 FROM t WHERE {CLASSIFY} <> 'red'",
    f"SELECT 1 FROM t WHERE {CLASSIFY} = 'green'",          # not one of its labels
    f"SELECT 1 FROM t WHERE {YES_NO} = 'yes'",              # AISQL compares the answer exactly
    f"SELECT 1 FROM t WHERE {CLASSIFY} = 'red' OR t.size > 3",
    f"SELECT 1 FROM u LEFT JOIN t ON u.id = t.id WHERE {CLASSIFY} = 'red'",
    f"SELECT * FROM t WHERE {CLASSIFY} = 'red'",
])
def test_shapes_without_a_sema_form_are_unsupported(query):
    with pytest.raises(Unsupported):
        to_sema(query)


QUESTIONS = [q for db in ("california_schools", "superhero", "formula_1", "european_football_2") for q in load_questions(db)]


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda q: q.qid)
def test_every_query_translates(question):
    out = to_sema(load_query("aisql", question.qid).strip().rstrip(";"), lambda table, column: ["A", "B"]).lower()
    assert "ai_filter" not in out and "ai_complete" not in out and "ai_classify" not in out and "ai_agg" not in out
    assert "s'" in out and "''" not in re.sub(r"'’', ''''", "", out).split("s'", 1)[1].split("'")[0]


@pytest.mark.parametrize("question", [q for q in QUESTIONS if "(SELECT list(" in load_query("aisql", q.qid)],
                         ids=lambda q: q.qid)
def test_label_subqueries_get_the_databases_labels(question):
    if not paths.database_path(question.db, masked=True).is_file():
        pytest.skip("run `swan-bench prepare` first")
    out = to_sema(load_query("aisql", question.qid).strip().rstrip(";"), sema.SemaSystem._labels(question.db))
    labels = out.split("Answer with exactly one of: ", 1)[1].split(".'", 1)[0].split(", ")
    assert len(labels) > 2 and labels == sorted(labels) and all(label.strip() == label and label for label in labels)
    assert ("’" in out) == ("replace(trim(" in out)  # a label with an apostrophe comes back as the database spells it


def _fake_shell(tmp_path, monkeypatch, body):
    """A stand-in for Sema's shell (a Python script given the shell's arguments and script), and no conversion."""
    shell = tmp_path / "duckdb"
    shell.write_text("#!/usr/bin/env python3\nimport re, sys\nargs, script = sys.argv[1:], sys.stdin.read()\n"
                     "once = re.findall(r'^\\.once (.+)$', script, re.M)\n" + body)
    shell.chmod(0o755)
    monkeypatch.setattr(sema, "ensure_duckdb", lambda db, binary, target=None: tmp_path / "db.duckdb")
    return sema.SemaSystem("the 'model'", "http://127.0.0.1:9", sema_bin=str(shell), batch=4)  # no such option: ignored


def test_the_adapter_runs_sema_at_its_default_settings_and_reads_its_rows(tmp_path, monkeypatch):
    seen = tmp_path / "seen.txt"
    system = _fake_shell(tmp_path, monkeypatch, f"open({str(seen)!r}, 'w').write(repr(args) + '\\n' + script)\n"
                         "open(once[0], 'w').write('[{\"column_name\": \"n\", \"column_type\": \"HUGEINT\"}]')\n"
                         "open(once[1], 'w').write('[{\"n\": \"170141183460469231731687303715884105727\"}]')\n")
    question = QUESTIONS[0]
    rows = system.execute(question, f"SELECT t.name FROM t WHERE {FILTER}")
    assert rows == [(170141183460469231731687303715884105727,)]  # a number the shell prints as text
    args, script = seen.read_text().split("\n", 1)
    assert args == repr(["-init", "/dev/null", "-readonly", "-bail", str(tmp_path / "db.duckdb")])  # no ~/.duckdbrc
    assert set(re.findall(r"^SET (\w+)=", script, re.M)) == {"llm_url", "llm_model", "llm_api_key", "threads"}
    assert "SET llm_model='the ''model''';" in script and "SET threads=1;" in script and "s'Is it big? {t.name}'" in script
    assert system.answers_failed_calls and not hasattr(system, "batch")
    # the adapter's own source sets nothing else of Sema's either
    source = inspect.getsource(sema)
    assert set(re.findall(r"SET (\w+)=", source)) == {"llm_url", "llm_model", "llm_api_key", "threads"}
    assert "semantic_" not in source.replace("semantic_batch_size 1", "")  # named once, in the docstring


def test_a_query_sema_cannot_run_is_an_error_not_an_empty_answer(tmp_path, monkeypatch):
    system = _fake_shell(tmp_path, monkeypatch, "sys.stderr.write('Binder Error: no such table\\n')\nsys.exit(1)\n")
    with pytest.raises(RuntimeError, match="no such table"):
        system.execute(QUESTIONS[0], f"SELECT t.name FROM t WHERE {FILTER}")


def test_a_relative_path_to_the_shell_still_works_from_a_querys_own_directory(tmp_path, monkeypatch):
    (tmp_path / "duckdb").write_text("")
    monkeypatch.chdir(tmp_path)
    assert sema.SemaSystem("model", "http://127.0.0.1:9", sema_bin="duckdb").sema_bin == str((tmp_path / "duckdb").resolve())
