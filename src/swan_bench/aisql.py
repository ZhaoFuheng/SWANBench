"""The SWAN 2.0 query language: DuckDB SQL with AI functions in one fixed prompt form.

Every question has one AISQL query (`queries/aisql/<qid>.sql`). SWAN-AISQL runs it as written; the BlendSQL
and LOTUS translators (`translate_blendsql.py`, `systems/lotus.py`) derive their programs from it. For those
translations to be mechanical, each AI call has one of these forms:

    ai_filter('Context:\n[<name>]: «' || <alias>.<name> || '»\n\n\nClaim: <question> <name>')
    ai_complete('<question> <name>: ' || <alias>.<name> [|| ' <suffix>'])
    ai_classify('<question> <name>: ' || <alias>.<name>, ['<label>', ...])
    ai_classify('<question> <name>: ' || <alias>.<name>, (SELECT list(DISTINCT <col> ORDER BY <col>) FROM <table>))
    ai_agg(list(<alias>.<name>), '<instruction>')

The context is one column; a composite key is built in a CTE first. `ai_filter`'s prompt spells out the claim
layout (`\n` is a newline in the SQL text): the context as `[<name>]: «<value>»`, then `Claim: <question> <name>`.
The translators read the question and the context column back out of it, so BlendSQL and LOTUS lay them out in
their own way (QUESTION/CONTEXT, Claim/Context); the layout measured best for SWAN-AISQL's filter. In the other
calls `<name>` in the literal is the context column's name, so every system shows the model the same
`<name>: <value>` context. The suffix is one of
`SUFFIXES`. `ai_agg` appears only as the aggregate of a scalar subquery or of a query without GROUP BY.
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

AI_FUNCTIONS = ("ai_filter", "ai_complete", "ai_classify", "ai_agg")
SUFFIXES = (" Answer with the number only.", " Answer with the date only, in YYYY-MM-DD format.",
            " Answer with the value only, without any other words.")


class AISQLFormError(ValueError):
    pass


class Unsupported(AISQLFormError):
    """The query uses something a system's dialect cannot express; the harness records the question as unsupported."""



@dataclass
class AICall:
    node: exp.Expression
    fn: str
    question: str = ""
    name: str = ""
    context: exp.Column | None = None
    suffix: str = ""
    labels: list[str] | None = None
    labels_from: tuple[str, str] | None = None  # (table, column) of a label subquery
    instruction: str = ""

    @property
    def prompt_prefix(self) -> str:
        """`<question> <name>: `, the text before the value, exactly as SWAN-AISQL sends it."""
        return f"{self.question} {self.name}: "


def parse(query: str) -> exp.Expression:
    return sqlglot.parse_one(query.strip().rstrip(";"), read="duckdb")


def _fn_name(node: exp.Expression) -> str | None:
    if isinstance(node, exp.AIClassify):
        return "ai_classify"
    if isinstance(node, exp.AIAgg):
        return "ai_agg"
    if isinstance(node, exp.Anonymous) and node.name.lower().startswith("ai_"):
        return node.name.lower()  # any other ai_* function is rejected by ai_calls
    return None


def _args(node: exp.Expression) -> list[exp.Expression]:
    """An AI call's arguments, whether sqlglot parsed it as a generic call or as its own AI node type."""
    if isinstance(node, exp.AIClassify):
        return [a for a in (node.this, node.args.get("categories"), node.args.get("config")) if a is not None]
    if isinstance(node, exp.AIAgg):
        return [node.this, node.expression]
    return list(node.expressions)


def _concat_parts(node: exp.Expression) -> list[exp.Expression]:
    if isinstance(node, exp.DPipe):
        return _concat_parts(node.this) + _concat_parts(node.expression)
    if isinstance(node, exp.Paren):
        return _concat_parts(node.this)
    return [node]


def _prompt(call: AICall, arg: exp.Expression) -> None:
    parts = _concat_parts(arg)
    if len(parts) not in (2, 3) or not isinstance(parts[0], exp.Literal) or not parts[0].is_string:
        raise AISQLFormError(f"{call.fn}: the prompt must be '<question> <name>: ' || <column> [|| '<suffix>']")
    if not isinstance(parts[1], exp.Column):
        raise AISQLFormError(f"{call.fn}: the context must be one column (build a composite key in a CTE)")
    head, column = parts[0].this, parts[1]
    name = column.meta.get("swan_name", column.name)  # an interpreter may rename the column; see lotus_exec
    if not head.endswith(f" {name}: "):
        raise AISQLFormError(f"{call.fn}: the literal must end with ' {name}: ' (the context column's name)")
    call.question, call.name, call.context = head[: -len(f" {name}: ")], name, column
    if not call.question.strip():
        raise AISQLFormError(f"{call.fn}: empty question")
    if len(parts) == 3:
        if not isinstance(parts[2], exp.Literal) or parts[2].this not in SUFFIXES:
            raise AISQLFormError(f"{call.fn}: the suffix must be one of {SUFFIXES}")
        call.suffix = parts[2].this


CLAIM_HEAD, CLAIM_MID = "Context:\n[{name}]: «", "»\n\n\nClaim: "
CLAIM_FORM = "ai_filter('Context:\\n[<name>]: «' || <alias>.<name> || '»\\n\\n\\nClaim: <question> <name>')"


def _claim_prompt(call: AICall, arg: exp.Expression) -> None:
    """ai_filter's prompt: 'Context:\n[<name>]: «' || <column> || '»\n\n\nClaim: <question> <name>'."""
    parts = _concat_parts(arg)
    if len(parts) != 3 or not all(isinstance(p, exp.Literal) and p.is_string for p in (parts[0], parts[2])) \
            or not isinstance(parts[1], exp.Column):
        raise AISQLFormError(f"ai_filter: the prompt must be {CLAIM_FORM}")
    column = parts[1]
    name = column.meta.get("swan_name", column.name)
    head, tail = parts[0].this, parts[2].this
    if head != CLAIM_HEAD.format(name=name) or not tail.startswith(CLAIM_MID) or not tail.endswith(f" {name}"):
        raise AISQLFormError(f"ai_filter: the prompt must be {CLAIM_FORM}, with <name> the context column's name")
    call.question, call.name, call.context = tail[len(CLAIM_MID): -len(f" {name}")], name, column
    if not call.question.strip():
        raise AISQLFormError("ai_filter: empty question")


def ai_calls(tree: exp.Expression) -> list[AICall]:
    """Every AI call in the query, validated, in the order it appears in the text."""
    calls = []
    for node in tree.walk():
        fn = _fn_name(node)
        if fn is None:
            continue
        if fn not in AI_FUNCTIONS:
            raise AISQLFormError(f"{fn} is not part of the SWAN 2.0 query language ({', '.join(AI_FUNCTIONS)})")
        call, args = AICall(node, fn), _args(node)
        if fn == "ai_filter":
            if len(args) != 1:
                raise AISQLFormError("ai_filter takes one prompt")
            _claim_prompt(call, args[0])
        elif fn == "ai_complete":
            if len(args) != 1:
                raise AISQLFormError(f"{fn} takes one argument")
            _prompt(call, args[0])
        elif fn == "ai_classify":
            if len(args) != 2:
                raise AISQLFormError("ai_classify takes a prompt and a label list")
            _prompt(call, args[0])
            if call.suffix:
                raise AISQLFormError("ai_classify takes no suffix")
            labels = args[1]
            if isinstance(labels, exp.Array) and all(isinstance(e, exp.Literal) and e.is_string
                                                     for e in labels.expressions):
                call.labels = [e.this for e in labels.expressions]
            elif isinstance(labels, exp.Subquery):
                call.labels_from = _label_subquery(labels.this)
            else:
                raise AISQLFormError("ai_classify labels: a list of string literals or "
                                     "(SELECT list(DISTINCT c ORDER BY c) FROM t)")
        else:  # ai_agg
            if len(args) != 2 or not isinstance(args[1], exp.Literal) or not args[1].is_string:
                raise AISQLFormError("ai_agg takes list(<column>) and a literal instruction")
            inner = args[0]
            if not (isinstance(inner, (exp.Anonymous, exp.List, exp.ArrayAgg)) and len(_agg_args(inner)) == 1
                    and isinstance(_agg_args(inner)[0], exp.Column)):
                raise AISQLFormError("ai_agg's first argument must be list(<column>)")
            column = _agg_args(inner)[0]
            call.context, call.name, call.instruction = column, column.meta.get("swan_name", column.name), args[1].this
        calls.append(call)
    return calls


def _agg_args(node: exp.Expression) -> list[exp.Expression]:
    if isinstance(node, exp.ArrayAgg):
        return [node.this]
    return list(node.expressions)


def _label_subquery(select: exp.Expression) -> tuple[str, str]:
    ok = isinstance(select, exp.Select) and len(select.expressions) == 1
    agg = select.expressions[0] if ok else None
    tables = list(select.find_all(exp.Table)) if ok else []
    if not ok or not isinstance(agg, (exp.ArrayAgg, exp.Anonymous, exp.List)) or len(tables) != 1:
        raise AISQLFormError("ai_classify label subquery must be (SELECT list(DISTINCT c ORDER BY c) FROM t)")
    column = next(agg.find_all(exp.Column), None)
    if column is None:
        raise AISQLFormError("ai_classify label subquery must name a column")
    return tables[0].name, column.name


def lint(query: str) -> list[str]:
    """Problems that stop a query from being part of SWAN 2.0 (empty when it is fine)."""
    try:
        tree = parse(query)
    except sqlglot.errors.ParseError as ex:
        return [f"does not parse: {ex}"]
    try:
        calls = ai_calls(tree)
    except AISQLFormError as ex:
        return [str(ex)]
    problems = []
    if not calls:
        problems.append("no AI call: every SWAN question needs the LLM")
    for call in calls:
        if call.fn == "ai_agg":
            select = call.node.find_ancestor(exp.Select)
            if select is not None and select.args.get("group"):
                problems.append("ai_agg inside a GROUP BY query is not supported by the translators")
    for node in tree.walk():
        if isinstance(node, exp.Cast) and node.to.this in (exp.DataType.Type.FLOAT,):
            problems.append("CAST AS REAL/FLOAT is 32-bit in DuckDB: use DOUBLE")
    return problems
