# Changes since the 2024 release

## 2.0.0: SWAN 2.0

SWAN 2.0 measures how well a system plans its LLM calls, as well as its answers (docs/SWAN2_DESIGN.md).

- **One query per question, run by every system.** Each question has one AISQL query, written plainly.
  SWAN-AISQL runs it as is; BlendSQL runs a mechanical translation (`translate_blendsql.py`); LOTUS runs the
  program that follows the query's written order (`lotus_exec.py`). No query author shapes a system's plan.
  SWAN 1.x's hand-written per-system queries are archived in `swan1/`.
- **The 120 questions are rewritten** into ten shapes that leave room for a planner (docs/SWAN2_AUTHORING.md),
  30 per database, with new gold queries. Each keeps its id and the kind of hidden value it needs. About 20
  have 3 to 4 AI filters.
- **Scaled and duplicated databases.** `swan-bench prepare` builds them from BIRD with a fixed seed
  (`data/swan2.json`): tables over 10,000 rows that the questions read are sampled to about 10,000 rows, and
  each prompt entity gets `1 + Poisson(1)` identical copies with new ids. BIRD's databases move to
  `data/databases/bird/`.
- **Oracle queries.** `queries/oracle/<qid>.sql` is the AISQL query with the true values in place of the AI
  calls; a test checks that it returns the gold answer.
- **Shadow model.** `swan-bench check` runs all three systems on a deterministic stand-in model that answers
  each (question, value) pair the same way whatever the system's prompt format; their results must be
  identical. `swan-bench lint` checks each query's prompt form.
- **Any-k scoring.** A question whose query ends in LIMIT k without ORDER BY accepts any k valid rows.
- **Questions per database.** `data/questions.csv` became `data/questions/<db>.csv`, with a `shape` column.
- **`ai_filter` prompts spell out a claim layout** (`Context:` / `[<name>]: «<value>»` / `Claim: <question>
  <name>`), the layout that measured best for SWAN-AISQL's filter on these questions; the translators read
  the question and the context column back out of it, so BlendSQL's and LOTUS's prompts are unchanged.
- **Run scripts.** `scripts/run_{swan_aisql,blendsql,lotus}.sh` go from a fresh clone to results.
- **Results refreshed (2026-10-02)** with SWAN-AISQL's `ai_filter` reasoning field and zero-shot BlendSQL:
  `results/gpt-5.6-luna/README.md`.
- **Results are replays of one published cache, with latency (2026-10-03).** All four systems ran the 120
  questions fresh, one after the other, through an empty recording cache; the recording is the published
  replay cache, and `results/gpt-5.6-luna/` holds each system's replay of it with the recorded latencies, so
  quality, calls, cost and latency (seconds per question, totals per database) come from one recording and
  reproduce. The earlier separate runs (0.775 / 0.741 / 0.770 / 0.739 for SWAN-AISQL / BlendSQL / LOTUS /
  PLOP) stay in its README as the reference for what a fresh run moves.
- **Palimpzest results (2026-10-04):** `results/gpt-5.6-luna/palimpzest/`, quality 0.769 (60 exact) at 70,645
  calls, $9.76 and 2,799 s over the 120 questions, recorded on 2026-10-04 through the same proxy (its latency
  therefore carries another day's provider load); on ThalamusDB's 69 questions 0.725.
- **Palimpzest as a sixth system (2026-10-04).** `swan-bench run --system palimpzest` runs the query in written
  order (as for LOTUS) with every AI call served by Palimpzest 1.5.3 under its Abacus optimizer (pareto plan
  search; the sample-based cost model needs a validator the benchmark does not supply), through a
  long-lived server in Palimpzest's own environment (`scripts/setup_palimpzest.sh`).
- **ThalamusDB results (2026-10-04):** `results/gpt-5.6-luna/thalamusdb/`, 0.689 on the 69 questions it
  expresses (0.396 over all 120), 158,290 calls; its README gives every system on the same 69.
- **ThalamusDB as a fifth system (2026-10-03).** `swan-bench run --system thalamusdb` translates every
  WHERE-clause `ai_filter` into ThalamusDB's `NLfilter` (`translate_thalamusdb.py`) and runs it in
  ThalamusDB's own environment (`scripts/setup_thalamusdb.sh`, `thalamusdb_runner.py`) with its stop
  conditions lifted, so its result is exact. Questions a dialect cannot express are now recorded as
  unsupported (score 0) and `scores.json` carries the mean over the supported ones as well.
- **PLOP as a fourth system (2026-10-03).** `swan-bench run --system plop --plop-bin <Morrila duckdb>` translates
  every query into PLOP's `semantic()` dialect (`translate_plop.py`) and runs it on the authors' fork over a
  parquet export of the databases; calls, tokens and an estimated cost come from the fork's log.
- **BlendSQL is zero-shot too (2026-10-02).** Its `LLMMap` prompt carries a built-in one-shot example ("Is this city
  in the California Bay Area? ... True"); the adapter strips it, so no system sees an example.
- **Review of the 120 questions (2026-10-01).** Answers are now about entities, not rows: every query counts
  and lists distinct entities, so the build's copies change the work but not the answer. The mask now also
  hides `schools.Charter`, `FundingType`, `EdOpsName`, `EdOpsCode` and `SOCType`, which stated the hidden
  facts outright. Football height claims say "at least X cm" (heights are inch-converted, so "taller than
  185 cm" was undecidable for players listed as 185 cm). Two gold queries were wrong and are fixed:
  superhero-25 averaged weights of 0 (missing), and european_football_2-15 silently dropped players without
  an attribute record (now stated in the question). Scoring compares URLs without scheme, `www.`,
  percent-encoding and trailing slash, which no model can know.
- **Removed as SWAN 1.x leftovers:** BlendSQL's `--shots` few-shot examples, `data/table_keys.json` and the
  2024 migration scripts (both moved to `swan1/`), and the per-system query directories.

**Known BlendSQL 0.1.27 limitations** found while writing the queries; the queries avoid them:

- A negated ingredient (`NOT {{LLMMap(...)}} = TRUE`) makes no calls; the translator writes `= FALSE`.
- Parentheses around an ingredient comparison, `t.*` in a CTE with an ingredient, an OR of ingredients on an
  aliased table, and an ingredient in a CASE on an aliased table lose the table alias ("no such table").
- The return type is inferred from nearby columns (a CASE condition's type); the translator passes
  `return_type='str'`.
- A self-join of the table an ingredient reads is ambiguous.

**SWAN-AISQL issues** found the same way: `SELECT DISTINCT` of an AI-filtered column over a join, and a
negated AI filter beside a second AI filter on the same CTE key in a three-way join, failed with "INTERNAL
Error: Failed to bind column reference". Both are fixed in SWAN-AISQL (commit b59500b); the queries still
use the `IN (subquery)` forms written around them.

## 0.3.0: three systems, HQDL removed

- **HQDL removed.** The HQDL baseline scored stored 2024 generations, and it had scoring bugs. Its
  generations could not be regenerated, because the code that built the requests was never committed. Its
  code, prompts, requests, generations and `prefix_keys` remain in the git history (release 0.2.0).
- **Queries moved to files.** Each system's query per question is now `queries/<system>/<qid>.{sql,py}`.
  `data/questions.csv` keeps only the question, its evidence and the gold query.
- **LOTUS and SWAN-AISQL queries added.** They port the BlendSQL queries (`queries/README.md`).
- **One runner for every system.** `swan-bench run --system {blendsql,lotus,aisql}` replaces
  `blendsql-run`. Every system's LLM traffic passes through a local meter (`src/swan_bench/meter.py`),
  which counts calls, tokens and cost the same way for all of them. The default model is `gpt-5.6-luna`,
  through the SWAN-AISQL cache proxy.
- **SemBench-style quality score.** Each question is scored 0 to 1: relative error for a single-number
  answer, set F1 over rows otherwise. Exact match, the 2024 metric, is still reported.
- **Floats compare to 10 significant digits.** The gold query runs in sqlite, and SWAN-AISQL (DuckDB) or
  LOTUS (pandas) may compute the same number with different last bits.
- **BlendSQL options.** california_schools-00 passed its options as `'A;B;C'`, the 0.0.x form. BlendSQL
  0.1.x reads a string option character by character, so the model was offered single letters. The options
  are now a tuple, and `swan1/scripts/blendsql_2024_syntax.py` has the rule.
- **BlendSQL booleans.** european_football_2-08, -18 and -19 compared an LLMMap in the SELECT list with
  `'t'`, the way BlendSQL 0.0.x stored a boolean answer. BlendSQL 0.1.x stores the model's free text
  there, so the comparison never matched. The LLMMap is now written `{{LLMMap(...)}} = TRUE`, which
  0.1.x answers as a yes/no question and stores as 1 or 0, and the comparison is `= TRUE`.

### Known issues in the query logic (not changed)

Porting the queries to LOTUS and SWAN-AISQL surfaced BlendSQL queries whose logic does not fully answer
the question or match the gold query. They were left as written, so all three systems share them, and
they lower every system's accuracy alike. They are candidates for a later data revision.

| question | issue |
|---|---|
| california_schools-10 | matches school names in Contra Costa; the gold query filters `satscores.cname` |
| california_schools-15, -27 | miss the gold query's `Website IS NOT NULL` / `School IS NOT NULL` |
| california_schools-22 | selects `AvgScrMath`; the question asks for `AvgScrWrite` |
| california_schools-23 | `COUNT(DISTINCT School)`; the gold query uses `COUNT(School)` |
| superhero-00, -19, -24, -26 | `options=superpower.power_name` limits a hero to one power, where the question needs all of them |
| superhero-14 | the Marvel CTE has COUNT(*) without GROUP BY, so it keeps one arbitrary hero |
| formula_1-01 | lacks the gold query's DISTINCT |
| formula_1-07 | returns lat and lng; the gold query also returns the location |
| formula_1-11 | lacks the gold query's `dob IS NOT NULL` |
| european_football_2-03 | sorts DESC; the gold query sorts ascending |
| european_football_2-05 | asks for goals > 0 rather than away goals minus home goals > 0 |
| european_football_2-08 | a percentage over Player rows; the gold query counts per-foot attribute rows |
| european_football_2-18 | `HAVING in_Netherlands` tests one arbitrary row per league |

## 0.2.0: from notebooks to a package

The 2024 release was a set of Jupyter notebooks, pickles and per-database CSV files. Version 0.2.0 turned it
into a Python package with a command-line tool and a uv environment. `swan1/scripts/migrate_2024_layout.py`
performed the conversion and is kept as the record of every data change listed here.

### Layout

| 2024 | 0.2.0 |
|---|---|
| `beyond-database-questions/*Queries.csv`, `*_HybridQueries.csv` (8 files, no header) | `data/questions.csv` (one row per question, with a header) |
| `databases/columns_to_drop.pickle` | `data/masked_columns.json` |
| `databases/db_table_keys.pickle` | `data/table_keys.json` |
| `databases/dev_databases.zip` | `data/databases/dev_databases.zip` |
| `HybridQuery/BlendSQLSwanEval.ipynb` | `swan-bench blendsql-run` (`src/swan_bench/blendsql_baseline/`) |
| `HybridQuery/logs/` | `results/blendsql_2024/` |

- The notebooks, pickles and CSV files remain in the git history.

### New: masked databases

In 2024 the masking was a list of columns that systems were asked not to use; nothing enforced it.
`swan-bench prepare` now builds a masked copy of each database with those columns removed. The BlendSQL
baseline runs on the masked copies.

### Data corrections

**Masked columns** (`columns_to_drop.pickle` → `masked_columns.json`):

- The California Schools table is `frpm`; the pickle said `fprm` for all six of its entries.
- `schools.Country` does not exist. It is now `schools.County`, the column that the HQDL `llm` table and
  the BlendSQL county questions derive.
- Two pairs of entries had been merged by a missing comma: `Country.name` + `Match.league_id`, and
  `hero_power.hero_id` + `hero_power.power_id`. They are now four separate entries.
- Nothing else changed. In particular `schools.Virtual` is still not masked, although the HQDL prompts
  and the BlendSQL questions derive it.

**Table keys** (`db_table_keys.pickle` → `table_keys.json`): carried over unchanged. Two entries name
columns their table does not have: `League` keyed on `league_id`, and `Country` keyed on `country_id`.
Those are `Match`'s foreign-key names. No code reads these keys.

**BlendSQL queries** (now `queries/blendsql/`; the originals are kept verbatim in `queries/blendsql_2024/`):

- **Syntax translated to BlendSQL 0.1.x** (`swan1/scripts/blendsql_2024_syntax.py`). None of these changes
  alters a query's meaning:
  - `'table::column'` and `(table::column)` became `table.column`.
  - A CTE named `temp` was renamed `temp_cte`: 0.1.x leaves `temp.column` references behind when it
    rewrites the CTE.
  - Inside an ingredient, a table is now referred to by its alias when the query aliases it.
  - `SELECT t.*` over a single table became `SELECT *`.
- **14 queries were missing the comma** between an ingredient's question and its column, for example
  `LLMMap('Does the hero has Super Strength?' 'T1::superhero_name')`. BlendSQL 0.0.x read the two
  strings as one, so these calls had no column argument. The comma is added.
- **formula_1 rows 22–25 were misaligned.** The queries stored for questions 22, 23 and 24 answered
  questions 23, 24 and 25. The query stored for question 25 was a near-duplicate of question 26's. Each
  question now has the query that answers it. Question 22 had no query, and one was written in 2026 in
  the style of the database's other nationality queries. The 2024 BlendSQL scores for these four
  formula_1 questions were computed against the wrong gold queries.
- **formula_1-23** put its `LLMQA` alone in a CTE, which BlendSQL 0.1.x cannot resolve. The same logic
  is now written with the `LLMQA` inline.
- **european_football_2-01 and -20 read masked columns in plain SQL.**
  - Question 01 read `Match.home_team_goal` and `Match.away_team_goal` to find drawn matches.
  - Question 20 read `Player.height`.
  - So part of each answer came from the original data, and both queries fail on the masked
    database. They now derive those values with `LLMMap`, as the database's other queries do.

After these changes, all 120 queries parse and run on the masked databases under BlendSQL 0.1.27
(`tests/test_blendsql_queries.py`, which uses a local stub model).

### BlendSQL 0.1.x versus the 2024 runs

- The 2024 runs used BlendSQL 0.0.x (0.0.27–0.0.31, going by the API and dates) with gpt-3.5-turbo.
- BlendSQL 0.1.x's `LLMMap` no longer accepts retrieved few-shot examples, so the 2024 per-database
  `LLMMap` example banks cannot be used. `--shots` now applies to `LLMQA` only.
- The 2024 logs contain pickled BlendSQL 0.0.x objects and need that version to load.
- For these reasons, and because of the query corrections above, new BlendSQL numbers are not directly
  comparable with the 2024 ones.
