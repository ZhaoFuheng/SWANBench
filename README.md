# SWAN 2.0

SWAN is a benchmark of **beyond-database questions**: 120 questions over four BIRD databases
(`california_schools`, `superhero`, `formula_1`, `european_football_2`) whose answers depend on columns that
have been removed from the database. A system must recover the missing values with an LLM and combine them
with SQL.

SWAN 2.0 also measures how well a system **plans** its LLM calls. Each question has one query in AISQL
(DuckDB SQL with `ai_filter`, `ai_classify`, `ai_complete` and `ai_agg`), and six systems run it:

- **SWAN-AISQL**, DuckDB with the `aisql` extension, runs it as written.
- **BlendSQL** ([parkervg/blendsql](https://github.com/parkervg/blendsql)) runs an automatic translation.
- **LOTUS** ([lotus-data/lotus](https://github.com/lotus-data/lotus)) runs an automatic translation into a
  LOTUS program.
- **PLOP** (Morrila, the plan-level optimizer of the PLOP paper; not yet released) runs an automatic
  translation into its `semantic()` dialect. It needs the authors' DuckDB fork: `--plop-bin` (see
  SWAN-AISQL's `aisql-bench/PLOP_FORK.md` for the edits the fork needs to talk to the proxy).
- **ThalamusDB** ([itrummer/thalamusdb](https://github.com/itrummer/thalamusdb), PyPI `thalamusdb` 0.1.15) runs
  an automatic translation into its `NLfilter` dialect, with its stop conditions lifted so its approximate
  processing runs to an exact result. Its dialect has boolean filters only, so it answers the 69 questions
  whose AI calls are all `ai_filter`s in a WHERE clause; the rest count as unsupported (score 0, reported
  apart). It runs in its own environment: `scripts/setup_thalamusdb.sh`.
- **Palimpzest** ([mitdbg/palimpzest](https://github.com/mitdbg/palimpzest), PyPI `palimpzest` 1.5.3, with its
  Abacus optimizer: pareto plan search with its default cost model, since its sample-based cost estimation
  needs a validator or training set the benchmark does not supply) runs the query the way the LOTUS program
  does, in written order, with every
  AI call handed to Palimpzest as a semantic operator it plans. It runs in its own environment:
  `scripts/setup_palimpzest.sh`.

The questions give a planner choices (LIMITs, several AI filters, AI calls through joins, ...), and the
databases repeat each entity about twice. docs/SWAN2_DESIGN.md explains the design and lists every knob.

## Quick start

You need [uv](https://docs.astral.sh/uv/), git and an OpenAI API key. For SWAN-AISQL you also need cmake,
ninja and a C++ compiler, to build it once.

```bash
git clone https://github.com/ZhaoFuheng/SWANBench && cd SWANBench
cp .env.example .env                  # put your OPENAI_API_KEY in .env (git-ignored)

scripts/run_swan_aisql.sh --qid superhero-05     # one question, to check the setup
scripts/run_blendsql.sh   --qid superhero-05
scripts/run_lotus.sh      --qid superhero-05

scripts/run_swan_aisql.sh                         # all 120 questions
scripts/run_blendsql.sh
scripts/run_lotus.sh
uv run swan-bench report                          # the systems side by side
```

Each script sets up what its system needs and skips what is already done: the Python environment and the
databases, a checkout of SWAN-AISQL next to this repository for its serving stack (litellm, the cache
proxy and, for SWAN-AISQL itself, its binary and embedding server), then the questions.

Arguments go to `swan-bench run`: `--qid` and `--db` pick questions, `--stub` runs with a local stand-in
instead of a model (free, no key, no servers; its answers are meaningless). Results go to
`runs/<system>/<model>/`. PLOP, ThalamusDB and Palimpzest have no script: PLOP's fork is not public, and the
other two need their own Python environments (`scripts/setup_thalamusdb.sh`, `scripts/setup_palimpzest.sh`);
run them as in step 5 below.

**The cache proxy** records every answer with its cost and latency, and replays them when the same request
comes again, so a rerun costs nothing and reports the same numbers. Model inference at temperature 0 is
treated as deterministic: the cache is the single source of answers and latencies for every system, and the
published results are replays of it. The recorded answers of all four
systems on all 120 questions are published with SWAN-AISQL (its `serve/fetch_cache.sh` downloads them from
Zenodo), so the results in this repository replay without a provider key. `SWAN_BENCH_ENDPOINT=http://localhost:4000`
uses litellm alone, without recording. litellm is needed either way: it adapts each system's request to the
model (OpenAI itself rejects some of the parameters BlendSQL and SWAN-AISQL send). Other variables:
`SWAN_AISQL_DIR` (where SWAN-AISQL is or goes) and `SWAN_AISQL_DUCKDB` (a binary already built).
Stop the servers with:

```bash
pkill -f ai_cache_server.py; pkill -f 'litellm --config'; pkill -f ai_embed_server.py
```

## Step by step

The scripts run these commands; use them directly to control each step.

**1. Install.**

```bash
uv sync --extra blendsql --extra lotus     # the benchmark, BlendSQL and LOTUS
```

For SWAN-AISQL, build it and point the benchmark at the binary (the SWAN-AISQL README has details):

```bash
git clone --recurse-submodules https://github.com/ZhaoFuheng/SWAN-AISQL ../SWAN-AISQL
(cd ../SWAN-AISQL && GEN=ninja make release)
export SWAN_AISQL_DUCKDB=$PWD/../SWAN-AISQL/build/release/duckdb
```

**2. Build the databases.**

```bash
uv run swan-bench prepare
```

| directory under `data/databases/` | contents | used by |
|---|---|---|
| `bird/<db>/` | the BIRD database as shipped | the build |
| `original/<db>/<db>.sqlite` | SWAN 2.0: large tables sampled, entities duplicated (`data/swan2.json`) | gold answers |
| `masked/<db>/<db>.sqlite` | the SWAN 2.0 database without the hidden columns | the systems |

**3. Check the setup (free).**

```bash
uv run pytest                               # data, oracle answers, translations, scoring
uv run swan-bench lint                      # every AISQL query is in the form the translators need
uv run swan-bench check --db formula_1      # SWAN-AISQL, BlendSQL and LOTUS agree on a deterministic stand-in model
```

`check` runs SWAN-AISQL, BlendSQL and LOTUS with a shadow model that answers each (question, value) pair the same way
whatever the system, so their results must be identical. It also prints each system's LLM calls per question.

**4. Start the servers.** In `../SWAN-AISQL`, with its `.env` holding the key and its Python environment
(`uv venv && uv pip install -r requirements.txt -r requirements-embed-st.txt`) on the PATH:

```bash
serve/start_stack.sh          # litellm :4000, cache proxy :4001, embedding server :4002
```

**5. Run.**

```bash
uv run swan-bench run --system aisql
uv run swan-bench run --system blendsql
uv run swan-bench run --system lotus
uv run swan-bench run --system plop --plop-bin /path/to/morrila/duckdb --duckdb-bin "$SWAN_AISQL_DUCKDB"
scripts/setup_thalamusdb.sh && uv run swan-bench run --system thalamusdb --duckdb-bin "$SWAN_AISQL_DUCKDB"
scripts/setup_palimpzest.sh && uv run swan-bench run --system palimpzest
uv run swan-bench report
```

- Each question prints its time, LLM calls, cost and quality. `queries.jsonl` holds each question's answer
  and counts, and `scores.json` the totals per database.
- An interrupted run resumes where it stopped. `--retry-errors` reruns the questions that failed, and
  `--rerun` starts over.
- `--model` (default `gpt-5.6-luna`), `--endpoint`, `--concurrency` (default 20, for every system),
  `--budget`, `--set` and the other options are in the knobs table of docs/SWAN2_DESIGN.md.

## Scoring

Each question gets a quality score from 0 to 1, following SemBench (`src/swan_bench/quality.py`):

- one row holding one number: `1 - relative error`, floored at 0;
- "list any k" questions (LIMIT k without ORDER BY): precision over the rows returned, recall against at
  most k valid rows;
- anything else: F1 of the returned rows against the gold rows.

Rows match regardless of column order, floats to 10 significant digits, and URLs without their scheme,
`www.`, percent-encoding and trailing slash. A query that fails scores 0.

Besides quality and exact match, every run records per question the LLM calls, tokens, cost and **latency**
(wall-clock seconds, model response times included). Latency is reported from fresh runs, or from replays
with the proxy's latency replay on, which reproduce the recorded response times; a replay without it (the
quick-start runs through the cache) times the system's own work only. The cleanest comparison records the
systems back to back in one session, as the published results were.
The headline is the mean quality; exact match is reported too. `swan-bench rescore` recomputes both from a
run's stored answers. A question a system's dialect cannot express (ThalamusDB: anything but a WHERE-clause
`ai_filter`) scores 0 and is counted as unsupported; `scores.json` and `swan-bench report` also give the
mean over the supported questions.

## Results

`results/gpt-5.6-luna/` holds the four systems' answers, scores and seconds on gpt-5.6-luna, replayed from
the published cache: mean quality 0.763 for SWAN-AISQL at 22,327 LLM calls and 2,331 s over the
120 questions, 0.761 for BlendSQL at 59,620 calls and 4,608 s, 0.760 for LOTUS at 69,204 calls and
4,101 s, 0.690 for PLOP at 25,604 calls and 10,431 s, 0.766 for Palimpzest (Abacus optimizer) at 70,933
calls and 2,991 s, and 0.396 for ThalamusDB (0.689 on the 69 questions its filter-only dialect expresses) at
158,290 calls and 36,945 s. The model's verdicts bound the
quality column (that folder's README gives the earlier recordings); calls, cost and latency are the
separation.
SWAN 1.x results are in `swan1/results/`.

## Data

| path | contents |
|---|---|
| `data/questions/<db>.csv` | the questions: `qid`, `db`, `question`, `evidence`, `difficulty`, `shape`, `gold_sql` |
| `queries/aisql/<qid>.sql` | the query every system runs |
| `queries/oracle/<qid>.sql` | the same query with the true values in place of the AI calls; it returns the gold answer |
| `data/swan2.json` | the database build: seed, target size, mean copies, what is sampled and duplicated |
| `data/masked_columns.json` | the hidden columns |
| `data/databases/dev_databases.zip` | the four BIRD dev databases |

From Python:

```python
from swan_bench.data import load_questions, load_query

for q in load_questions("superhero"):
    print(q.qid, q.shape, q.question)
    print(load_query("aisql", q.qid))
```

Adding or editing a question: docs/SWAN2_AUTHORING.md.

## Repository layout

```
scripts/                 run_swan_aisql.sh, run_blendsql.sh, run_lotus.sh, setup_thalamusdb.sh, setup_palimpzest.sh
src/swan_bench/          the benchmark: database build, AISQL language, translators, meter, runner, scoring
src/swan_bench/systems/  one adapter per system (SWAN-AISQL, BlendSQL, LOTUS, PLOP, ThalamusDB, Palimpzest)
queries/                 the AISQL and oracle queries
swan1/                   SWAN 1.x: its questions, per-system queries, results and the 2024 migration scripts
results/gpt-5.6-luna/    SWAN 2.0 answers and scores for the four systems
results/blendsql_2024/   the 2024 BlendSQL logs (5-shot logs in Git LFS)
docs/                    SWAN2_DESIGN.md, SWAN2_AUTHORING.md
```

## License

MIT, see LICENSE. The databases come from the [BIRD benchmark](https://bird-bench.github.io/).
