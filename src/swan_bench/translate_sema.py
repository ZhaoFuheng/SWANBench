r"""Translate a SWAN 2.0 AISQL query into Sema's dialect (`s'...'` semantic expressions), mechanically.

| AISQL | Sema |
|---|---|
| `ai_filter(<claim: <question> on <alias>.<name>>)` | `s'<question> {<alias>.<name>}'` |
| `ai_complete('<question> <name>: ' \|\| <alias>.<name> [\|\| '<suffix>'])` | `s'<question> {<alias>.<name>}<suffix>'` |
| `ai_classify('<question> <name>: ' \|\| <alias>.<name>, <labels>)` | `trim(s'<question> {<alias>.<name>} Answer with exactly one of: <label>, <label>.')` |
| `ai_agg(list(<alias>.<name>), '<instruction>')` | `sem_agg('<instruction>', <alias>.<name>)` |

Sema lays each prompt out itself: the instruction keeps the `{column}` reference by name and the row's values go
in a separate context (`{name}: value`), the layout LOTUS's translation gets too (`<question> {<name>}`). Where the
filter, map or aggregate runs is Sema's decision: it plans the query. The labels of a label subquery are read
from the database first (as the LOTUS executor does) and written into the prompt. A semantic map answers with
text, so a classify answer is trimmed (LOTUS strips its answers the same way). `sem_agg` takes its instruction as
plain text and the column as its second argument. The rest of the query is DuckDB SQL, which Sema's fork of DuckDB
runs as is.

Sema types a semantic string by where it stands: in a query's WHERE it is a filter (BOOLEAN, read from a true or
false answer), anywhere else a map (text), and it cannot reference an outer query's columns from a subquery. Three
AISQL shapes therefore get their Sema form:

| AISQL | Sema |
|---|---|
| `ai_classify(..., ['Yes', 'No']) = 'Yes'` / `= 'No'` in WHERE (4 calls, 4 queries) | `s'<question> {<alias>.<name>}'` / `(NOT s'...')` (the yes/no question as a filter) |
| `ai_classify(...) = '<label>'` in WHERE, other labels (2 calls, 2 queries) | the classify map, computed in the FROM clause: `<table> AS <alias>` becomes `(SELECT <alias>.*, <map> AS sema_label_<n> FROM <table> AS <alias> WHERE <the conditions on that table alone>) AS <alias>`, and the comparison `<alias>.sema_label_<n> = '<label>'` |
| `ai_filter(...)` outside WHERE, as a CASE condition (2 calls, 2 queries) | `(lower(trim(s'<question> {<alias>.<name>} Answer with true or false only.')) = 'true')` (a map) |

A classification compared with a label stays a classification: a filter's true or false to "the answer is
<label>" is not the answer the classify gives (asked that way on california_schools-00, the model accepted a
quarter of the rows of the other labels). The map runs on the rows that its own table's plain conditions keep.

An apostrophe cannot stand inside `s'...'` (Sema keeps a doubled one as two characters in the prompt), so prompt
text carries the typographic one (’), and a classify answer is mapped back, so a label is returned as the database
spells it.
"""
from functools import reduce
from typing import Callable

from sqlglot import exp

from .aisql import AISQLFormError, Unsupported, _fn_name, ai_calls, parse

LABELS_LEAD = " Answer with exactly one of: "
TRUE_FALSE = " Answer with true or false only."
APOSTROPHE = "’"


def _in_where(node: exp.Expression) -> bool:
    """Whether Sema binds this expression as part of a query's WHERE clause (a filter), up to the enclosing query."""
    p = node.parent
    while p is not None and not isinstance(p, (exp.Select, exp.Subquery)):
        if isinstance(p, exp.Where):
            if isinstance(p.parent, exp.Filter):  # agg(...) FILTER (WHERE ...): Sema would bind a map there
                raise Unsupported("an AI call in an aggregate's FILTER clause has no Sema translation here")
            return True
        if isinstance(p, (exp.Join, exp.Having)):
            raise Unsupported("an AI call in a JOIN condition or HAVING has no Sema translation here")
        p = p.parent
    return False


def _literal(text: str) -> str:
    """Text inside an `s'...'` literal. No SWAN 2.0 question has braces; an apostrophe becomes the typographic one."""
    if "{" in text or "}" in text:
        raise AISQLFormError("a brace in a prompt would be read as a Sema expression")
    return text.replace("'", APOSTROPHE)


def _classify_map(call, labels: list[str]) -> str:
    """The classify as a semantic map: its answer trimmed, and spelled as the database spells the label."""
    lead = _literal(LABELS_LEAD + ", ".join(labels) + ".")
    text = f"trim(s'{_literal(call.question)} {{{call.context.sql('duckdb')}}}{lead}')"
    if any("'" in label for label in labels):
        text = f"replace({text}, '{APOSTROPHE}', '''')"
    return text


def _has_ai_call(node: exp.Expression) -> bool:
    return any(_fn_name(n) is not None or (isinstance(n, exp.Anonymous) and n.name.startswith("__sema_call_"))
               for n in node.walk())


def _conjuncts(node: exp.Expression) -> list[exp.Expression]:
    return list(node.flatten()) if isinstance(node, exp.And) else [node]


def _label_from_the_from_clause(cmp: exp.EQ, call, placeholder: exp.Expression, n: int) -> None:  # n: such rewrites so far
    """Rewrite `ai_classify(...) = '<label>'`, a conjunct of a WHERE, in place: the table its column comes from
    becomes a derived table (same alias) that adds the classify map as a column, and the comparison reads that
    column. The plain conditions on that table alone move into the derived table, so the map runs on the rows they
    keep."""
    alias, select = call.context.table, cmp.find_ancestor(exp.Select)
    where = select.args.get("where") if select is not None else None
    conjuncts = _conjuncts(where.this) if where is not None else []
    if not alias or not any(c.unnest() is cmp for c in conjuncts):
        raise Unsupported("ai_classify(...) = '<label>' under OR or NOT has no Sema translation here")
    joins = select.args.get("joins") or []
    if any((j.side or "") or (j.kind or "").upper() not in ("", "INNER", "CROSS") for j in joins):
        raise Unsupported("ai_classify(...) = '<label>' beside an outer join has no Sema translation here")
    if any(e.is_star for e in select.expressions):
        raise Unsupported("ai_classify(...) = '<label>' in a SELECT * has no Sema translation here")
    source = select.args.get("from_") or select.args.get("from")  # the key's name depends on the sqlglot version
    tables = [t for t in [source.this, *(j.this for j in joins)] if t.alias_or_name == alias]
    if len(tables) != 1:
        raise Unsupported(f"ai_classify(...) = '<label>': the table of {alias} is not in this FROM clause")
    table, column = tables[0], f"sema_label_{n}"

    def own(c: exp.Expression) -> bool:  # a plain condition on this table alone
        columns = list(c.find_all(exp.Column))
        return (c.unnest() is not cmp and not _has_ai_call(c) and c.find(exp.Select) is None and columns
                and all(col.table == alias for col in columns))

    moved = [c for c in conjuncts if own(c)]
    inner = exp.select(exp.Column(this=exp.Star(), table=exp.to_identifier(alias)),
                       exp.alias_(placeholder, column)).from_(table.copy())
    for c in moved:
        inner = inner.where(c.copy())
    label = exp.EQ(this=exp.column(column, table=alias), expression=exp.Literal.string(_target(cmp, call).this))
    kept = [label if c.unnest() is cmp else c for c in conjuncts if not any(c is m for m in moved)]
    where.set("this", reduce(lambda a, b: exp.And(this=a, expression=b), kept))
    table.replace(exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier(alias))))


def _target(cmp: exp.Expression, call) -> exp.Literal:
    """The label literal an `ai_classify(...) = '<label>'` compares with."""
    other = (cmp.expression if cmp.this is call.node else cmp.this) if isinstance(cmp, exp.EQ) else None
    if not (isinstance(other, exp.Literal) and other.is_string):
        raise Unsupported("ai_classify inside WHERE other than = '<label>' has no Sema translation here")
    return other


def to_sema(query: str, resolve_labels: Callable[[str, str], list[str]] | None = None) -> str:
    """`resolve_labels(table, column)` returns the labels of an `ai_classify` label subquery."""
    tree = parse(query)
    replacements, label_columns = {}, 0
    for i, call in enumerate(ai_calls(tree)):
        key = f"__sema_call_{i}"
        placeholder, replace = exp.Anonymous(this=key), call.node
        if call.fn == "ai_agg":
            column = call.context.sql("duckdb") if call.context is not None else None
            if column is None:
                raise AISQLFormError("ai_agg needs a column")
            text = f"sem_agg('{call.instruction.replace(chr(39), chr(39) * 2)}', {column})"  # a plain SQL string
        else:
            column = call.context.sql("duckdb")
            in_where = _in_where(call.node)
            if call.fn == "ai_filter":
                if in_where:
                    text = f"s'{_literal(call.question)} {{{column}}}'"
                else:
                    text = f"(lower(trim(s'{_literal(call.question)} {{{column}}}{TRUE_FALSE}')) = 'true')"
            elif call.fn == "ai_complete":
                if in_where:
                    raise Unsupported("ai_complete inside WHERE has no Sema translation here")
                text = f"s'{_literal(call.question)} {{{column}}}{_literal(call.suffix)}'"
            else:  # ai_classify
                labels = call.labels
                if labels is None:
                    if resolve_labels is None:
                        raise AISQLFormError("a label subquery needs the database to resolve its labels")
                    labels = resolve_labels(*call.labels_from)
                text = _classify_map(call, labels)
                if in_where:
                    cmp = call.node.parent
                    target = _target(cmp, call).this
                    if target not in labels:  # AISQL compares the answer with the literal exactly
                        raise Unsupported(f"ai_classify(...) = '{target}': not one of its labels")
                    if sorted(labels) == ["No", "Yes"]:
                        # a yes/no question: = 'Yes' is the question as a filter, = 'No' its negation
                        text = f"s'{_literal(call.question)} {{{column}}}'"
                        text, replace = (text if target == "Yes" else f"(NOT {text})"), cmp
                    else:
                        _label_from_the_from_clause(cmp, call, placeholder, label_columns)
                        label_columns, replace = label_columns + 1, None
        replacements[key] = text
        if replace is not None:
            replace.replace(placeholder)
    sql = tree.sql("duckdb")
    for key, text in replacements.items():
        token = f"{key}()"
        if token not in sql:
            token = f"{key.upper()}()"
        if token not in sql:
            raise AISQLFormError(f"lost the placeholder of an AI call while translating ({key})")
        sql = sql.replace(token, text)
    return sql
