r"""Translate a SWAN 2.0 AISQL query into ThalamusDB's dialect, mechanically.

| AISQL | ThalamusDB |
|---|---|
| `ai_filter('Context:\n[name]: «' \|\| t.col \|\| '»\n\n\nClaim: Q name')` as a WHERE conjunct | `NLfilter(t.col, 'Q')` |

ThalamusDB (0.1.x) has two semantic operators, `NLfilter(table.column, condition)` and `NLjoin(...)`, both
boolean and both evaluated in a WHERE clause; the condition is asked about the cell value. So an
`ai_filter` becomes `NLfilter` over the same context column with the same question. Everything else,
`ai_classify`, `ai_complete`, `ai_agg` and an `ai_filter` anywhere but in a WHERE clause's boolean
structure (a CASE branch, a SELECT list), has no ThalamusDB form and raises `Unsupported`; the harness
records such a question as unsupported and the results README reports the macro over the supported ones
next to the macro over all 120.

Column names that are not plain identifiers ("School Name") are renamed to identifier-safe ones
(`School_Name`), because ThalamusDB writes column names unquoted into its working tables; the ThalamusDB
copy of each database carries the same renaming (`systems/thalamusdb.py`). CTEs and aliases are kept as
written; the runner (`thalamusdb_runner.py`) prepares them for ThalamusDB.
"""
import re

from sqlglot import exp

from .aisql import Unsupported, ai_calls, parse

_BOOLEAN_CONTEXT = (exp.And, exp.Or, exp.Not, exp.Paren)
_UNSAFE = re.compile(r"[^0-9A-Za-z_]")


def safe_name(name: str) -> str:
    """The identifier ThalamusDB's copy of the database uses for a column (unchanged when already plain)."""
    return _UNSAFE.sub("_", name)


def _in_where(node: exp.Expression) -> bool:
    """True when `node` sits in the boolean structure of a WHERE clause (AND / OR / NOT / parentheses only)."""
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Where):
            return True
        if not isinstance(parent, _BOOLEAN_CONTEXT):
            return False
        parent = parent.parent
    return False


def to_thalamusdb(query: str) -> str:
    tree = parse(query)
    calls = ai_calls(tree)
    for call in calls:
        if call.fn != "ai_filter":
            raise Unsupported(f"ThalamusDB has no equivalent of {call.fn}")
        if not _in_where(call.node):
            raise Unsupported("ThalamusDB evaluates NLfilter only in a WHERE clause")
    for call in calls:
        call.node.replace(exp.Anonymous(this="NLfilter",
                                        expressions=[call.context.copy(), exp.Literal.string(call.question)]))
    for ident in tree.find_all(exp.Identifier):
        if _UNSAFE.search(ident.name):
            ident.set("this", safe_name(ident.name))
            ident.set("quoted", False)
    return tree.sql("duckdb")
