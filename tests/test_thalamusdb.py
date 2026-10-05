"""The ThalamusDB translation turns WHERE-clause ai_filters into NLfilter and refuses what the dialect lacks."""

import pytest

from swan_bench.aisql import Unsupported
from swan_bench.data import load_query, load_questions
from swan_bench.translate_thalamusdb import to_thalamusdb

ALL = [q for db in ("california_schools", "superhero", "formula_1", "european_football_2") for q in load_questions(db)]


def test_filter_in_where_becomes_nlfilter_over_the_same_column():
    q = ("SELECT t.name FROM t WHERE t.x > 1 AND NOT ai_filter('Context:\n[name]: «' || t.name || '»\n\n\nClaim: Is it big? name')")
    out = to_thalamusdb(q)
    assert "ai_filter" not in out.lower()
    assert "NOT NLFILTER(t.name, 'Is it big?')" in out or "NOT NLfilter(t.name, 'Is it big?')" in out


def _first_query_with(text: str) -> str:
    return next(load_query("aisql", q.qid) for q in ALL if text in load_query("aisql", q.qid))


@pytest.mark.parametrize("marker", ["ai_classify(", "ai_complete(", "CASE WHEN ai_filter("])
def test_other_functions_and_filters_outside_where_are_unsupported(marker):
    with pytest.raises(Unsupported):
        to_thalamusdb(_first_query_with(marker).strip().rstrip(";"))


def test_column_names_with_spaces_become_identifier_safe():
    out = to_thalamusdb('SELECT f."School Name" FROM frpm AS f WHERE f."District Name" = \'x\' AND '
                        'ai_filter(\'Context:\n[school]: «\' || f.school || \'»\n\n\nClaim: Is it big? school\')')
    assert '"School Name"' not in out and "f.School_Name" in out and "f.District_Name" in out


@pytest.mark.parametrize("question", ALL, ids=lambda q: q.qid)
def test_every_query_translates_or_is_declared_unsupported(question):
    query = load_query("aisql", question.qid).strip().rstrip(";")
    try:
        out = to_thalamusdb(query)
    except Unsupported:
        return
    low = out.lower()
    assert "nlfilter(" in low and "ai_filter" not in low and "ai_classify" not in low and "ai_complete" not in low


def test_supported_share_is_the_filter_only_where_clause_questions():
    supported = 0
    for q in ALL:
        try:
            to_thalamusdb(load_query("aisql", q.qid).strip().rstrip(";"))
            supported += 1
        except Unsupported:
            pass
    assert supported == 69
