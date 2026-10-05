# SWAN 2.0 design

SWAN asks questions whose answers depend on values that were removed from a database, so a system must
recover them with an LLM and combine them with SQL. SWAN 1.x measured whether a system gets the answer
right. SWAN 2.0 also measures how well a system **plans** its LLM calls: a better engine should reach the same
quality with fewer calls, at lower cost, and sooner.

## Principles

1. **One query per question, planned by the engine.** Each question has a single query in AISQL, DuckDB SQL
   with AI functions, written the way a user would write it: conditions in the order the question states
   them, no hand deduplication, no hand reordering. Every system runs that query, so any difference in
   calls comes from the system's own planning.
2. **Same answer, different cost.** Each question has one gold answer. Skipping work may not change the
   answer, so a system can only win by being cheaper at equal quality.
3. **Room to plan.** Questions and data are shaped so that a planner has real choices to make: which filter
   to run first, how far a LIMIT reaches, which values repeat.

## How each system runs a question

| system | what it runs |
|---|---|
| SWAN-AISQL | the AISQL query, as written; its optimizer plans the LLM calls, ordering filters with a selectivity model fed by an embedding server |
| BlendSQL | an automatic translation of the query into BlendSQL (LLM ingredients in sqlite); BlendSQL plans it. Its `LLMMap` prompt is sent without BlendSQL's built-in one-shot example, so every system is zero-shot |
| LOTUS | an automatic translation into the LOTUS program that follows the query's written order, with semantic operators over DataFrames; LOTUS has no planner, so the program is its plan |
| PLOP | an automatic translation into Morrila's `semantic()` dialect (`translate_plop.py`), run on the authors' DuckDB fork in its DP cost-model mode over a parquet export of the databases; PLOP plans it. The fork is not released, so this system is optional and `swan-bench check` does not cover it (its prompts carry PLOP's own answer-format suffix) |
| ThalamusDB | an automatic translation into ThalamusDB's `NLfilter(table.column, condition)` dialect (`translate_thalamusdb.py`), run by `thalamusdb_runner.py` in ThalamusDB's own environment with its stop conditions lifted (`max_error = 0`, no call or time cap), so its approximate processing runs to the exact result; ThalamusDB plans it (rows in batches of 20 per filter, all rows after its own SQL pruning). Its dialect has boolean WHERE-clause filters only, so 69 of the 120 questions are expressible; the others are recorded as unsupported. The runner prepares what ThalamusDB cannot parse itself, without changing the question: aliased tables become same-named temporary tables, CTEs are materialised (a CTE with a semantic predicate is run through ThalamusDB first), and column names with spaces are renamed in its copy of the database. `swan-bench check` does not cover it (its prompt frame is its own) |
| Palimpzest | the query run in its written order as for LOTUS (`lotus_exec.py`), with every AI call handed to Palimpzest as a semantic-operator program over the rows it reaches (`sem_filter` / `sem_add_columns` / `sem_agg`, `palimpzest_ops_server.py`), whose physical plan its Abacus optimizer chooses (`optimizer_strategy=pareto`, MaxQuality, 20 workers; `--pz-optimizer none` turns the optimizer off) with its default cost model: Palimpzest runs its sample-based cost estimation only when given a validator or training set, which the benchmark does not supply, so no sampling calls are made. Palimpzest runs in its own environment as one server process per run; it prices the benchmark model at zero, so the meter's accounting is the one used. `swan-bench check` does not cover it |

The translations are part of the benchmark, so no query author shapes a system's plan. Two checks keep them
honest. A linter requires every AI call to have one fixed form built from a question and one context column;
`ai_filter` spells out a claim layout (`Context: [<name>]: «<value>»`, then `Claim: <question> <name>`), from
which the translators read the question and the column back. A **shadow model**, a deterministic stand-in for the LLM that answers the same
(question, value) pair the same way whatever the system's prompt format, runs SWAN-AISQL, BlendSQL and LOTUS on every
question: their results must be identical.

## Data

The databases are the four BIRD dev databases (`california_schools`, `superhero`, `formula_1`,
`european_football_2`), rebuilt with a fixed seed:

- **Scale.** Large tables that the questions read are sampled down to a target size. Rows that depend on a
  dropped row are dropped with it, so every reference still resolves.
- **Duplication.** Each entity whose values go into prompts (a school, hero, driver, team, player, match)
  gets a random number of identical copies with new ids, `1 + Poisson(mean - 1)`, so a mean of 2 and some
  entities repeated many times. Identical entities produce identical prompts: an engine that evaluates each
  distinct prompt once pays for it once. Answers are about entities: every query counts and lists distinct
  entities, so a copy changes the work, not the answer.
- **Masking.** The hidden columns, and every other column that states the same fact outright, are removed
  from a copy of each database. Systems see only that copy; gold answers come from the full one.

## Questions

There are 30 questions per database. Each keeps the kind of hidden value its SWAN 1.x question needed and
has one of ten shapes:

| shape | per database | what a good engine exploits |
|---|---|---|
| `plain` | 6 | nothing special |
| `any_k` | 3 | "list any k": stop after k passing rows |
| `top_n` | 3 | ORDER BY a plain column, LIMIT k, AI output in SELECT: call the LLM only for the k rows kept |
| `multi_filter` | 5 | 3 to 4 AI filters, some written least selective first: order them, and run later ones only on survivors |
| `boolean` | 2 | AI filters under AND/OR: stop once a branch decides the row |
| `cheap_after` | 3 | a cheap, selective plain filter written after an AI filter: run it first |
| `join_fanout` | 3 | an AI call on a dimension's attribute seen through many fact rows: one call per distinct value |
| `cte_reuse` | 2 | a CTE with AI calls read twice: evaluate once |
| `case` | 2 | an AI call under `CASE WHEN <plain condition>`: only rows that reach the branch |
| `distinct_exists` | 1 | DISTINCT or EXISTS over an AI-filtered join: stop at the first match |

Each question also has an **oracle query**: the AISQL query with every AI call replaced by the true hidden
value. It must return the gold answer, which shows the AISQL query is right whenever the model is right.

## Scoring and measurement

- **Quality** per question, following SemBench: `1 - relative error` (floored at 0) for a single-number
  answer; for "any k" questions, precision over the rows returned with recall against at most k valid rows;
  otherwise set F1 of the rows against the gold rows. Values match regardless of column order, floats to 10
  significant digits, and URLs without the scheme, `www.`, percent-encoding and trailing slash that a model
  cannot know. The headline is the mean over questions. Exact match is reported as well. A question a
  system's dialect cannot express scores 0 and is counted as unsupported; the mean over the supported
  questions is reported next to it (`quality_supported` in `scores.json`).
- **Calls, tokens, cost and latency** are counted the same way for every system by a local meter between
  the system and the model endpoint. Every system gets the same model and the same number of requests in
  flight, and questions run one at a time. Latency is the wall-clock time of a question from the system's
  start to its last row (`seconds` in `queries.jsonl`, summed in `scores.json` and `swan-bench report`), so
  it includes the model's response times. A latency figure comes from a fresh run or from a replay with
  the proxy's latency replay on, which reproduces the recorded response times; the cleanest comparison
  records the systems back to back in one session, as the published results were. A replay with latency
  replay off measures the system's own work only (planning, data, dispatch), which is what the quick-start
  runs through the cache show.
- **Recording and replay.** Runs go through the SWAN-AISQL cache proxy, which records each answer with its
  cost and latency. Model inference at temperature 0 is treated as deterministic, so one cache is the single
  source of answers and latencies for every system: replaying a run costs nothing and reproduces its
  answers, cost and latency exactly, the systems can be compared side by side and rescored later, and a run
  that asks the provider afresh is a different experiment (the model does not in fact answer identically
  every time, so fresh runs move single-answer questions).

## Knobs

| knob | where | default | what it controls |
|---|---|---|---|
| `seed` | `data/swan2.json` | 2026 | every random choice in the database build |
| `target_rows` | `data/swan2.json` | 10,000 | the size large tables are sampled down to |
| `mean_copies` | `data/swan2.json` | 2 | the mean number of copies of each prompt entity |
| sample and duplicate rules | `data/swan2.json` | per database | which tables are sampled, which are duplicated, and which dependent rows follow a copy |
| masked columns | `data/masked_columns.json` | per database | the hidden columns |
| `--model` | `swan-bench run` | `gpt-5.6-luna` | the chat model, for every system |
| `--endpoint` | `swan-bench run`, `SWAN_BENCH_ENDPOINT` | `http://localhost:4001` | the OpenAI-compatible endpoint: the SWAN-AISQL cache proxy (records and replays), or litellm alone at `:4000` |
| `--concurrency` | `swan-bench run` | 20 | LLM requests in flight, for every system |
| `--budget` | `swan-bench run` | none | stop once the cache proxy has spent this many USD on new calls |
| `--set NAME=VALUE` | `swan-bench run` | none | a SWAN-AISQL setting before each query (e.g. `ai_pullup=false`) |
| `--duckdb-bin` | `swan-bench run`, `SWAN_AISQL_DUCKDB` | none | the SWAN-AISQL binary |
| `--plop-bin` | `swan-bench run`, `SWAN_PLOP_BIN` | none | the Morrila (PLOP) fork's shell; `plop` also needs `--duckdb-bin` for the parquet export |
| `--thalamusdb-python` | `swan-bench run`, `SWAN_THALAMUSDB_PYTHON` | `.venv-thalamusdb/bin/python` | the interpreter with thalamusdb installed (`scripts/setup_thalamusdb.sh`); `thalamusdb` also needs `--duckdb-bin` for the parquet export |
| `--palimpzest-python`, `--pz-optimizer`, `--pz-policy` | `swan-bench run`, `SWAN_PALIMPZEST_PYTHON` | `.venv-palimpzest/bin/python`, `pareto`, `MaxQuality` | the interpreter with palimpzest installed (`scripts/setup_palimpzest.sh`); Abacus on (`pareto`) or off (`none`); its policy |
| `SWAN_AISQL_DIR` | `scripts/run_*.sh` | `../SWAN-AISQL` | where the scripts find or clone SWAN-AISQL (binary, serving stack, embedding server) |
| `--db`, `--qid` | `swan-bench run`, `check`, `lint` | all | restrict to databases or questions |
| `--stub` | `swan-bench run` | off | answer with a local stub instead of a model, to test the setup for free |

After changing `data/swan2.json`, rebuild with `swan-bench prepare --force`. Gold answers are always computed
on the rebuilt data; run `pytest tests/test_oracle.py` to confirm every question still has a non-empty
answer that its oracle query reproduces.
