# SWAN 2.0 on gpt-5.6-luna

The files in this folder are replays of the published LLM cache (fetched by SWAN-AISQL's `serve/fetch_cache.sh`)
on one machine, a MacBook Air M5, in November 2026: each system ran the 120 questions alone, 20 requests in
flight, through the cache proxy, which returned every recorded answer after its recorded response time. No
provider was reachable during a run, so no figure here contains a fresh answer (ThalamusDB's run is the one
exception: a provider was reachable and no call went to it). Quality, calls, cost and latency therefore come
from one store and reproduce from it without a provider key. The store holds the most recent recording of each
prompt. The recordings are fresh runs
through an empty cache: LOTUS 1.2.4 and PLOP (Morrila, DP cost-model mode) in one session on 2026-10-03;
ThalamusDB 0.1.15 (exact mode) on 2026-10-03/04 (formula_1-22 and european_football_2-05 re-recorded on
2026-10-28 after the runner fix below); BlendSQL 0.1.27 and Palimpzest 1.5.3 (Abacus optimizer) on 2026-10-05;
SWAN-AISQL on 2026-10-23 (DuckDB 2.0.0, `ai_api = auto`). A prompt that a later run reached for the first time
was added when it occurred, the last on 2026-11-13; a prompt SWAN-AISQL's 2026-10-23 run did not ask keeps its
most recent earlier recording. Quality is defined in the repository README. Per system,
`answers.jsonl` holds each question's answer, calls, tokens, cost and seconds, and `scores.json` the totals per
database.

| database | LOTUS | BlendSQL | Palimpzest | ThalamusDB | PLOP-DP | SWAN-AISQL |
|---|---|---|---|---|---|---|
| california_schools | 0.733 | 0.717 | 0.756 | 0.292 | 0.685 | 0.712 |
| superhero | 0.474 | 0.498 | 0.536 | 0.288 | 0.511 | 0.527 |
| formula_1 | 1.000 | 0.984 | 0.970 | 0.532 | 0.902 | 0.991 |
| european_football_2 | 0.801 | 0.845 | 0.851 | 0.498 | 0.692 | 0.879 |
| **quality (mean)** | **0.752** | **0.761** | **0.778** | **0.403** | **0.698** | **0.777** |
| LLM calls | 69,204 | 59,620 | 70,925 | 160,525 | 25,639 | 18,747 |
| cost | $4.04 | $4.95 | $9.81 | $12.91 | $1.11 | $2.01 |
| latency, all 120 questions | 4,026 s | 4,958 s | 5,202 s | 24,729 s (69 q) | 10,208 s | 1,688 s |

ThalamusDB's dialect has boolean WHERE-clause filters only, so 51 questions (those with `ai_classify`,
`ai_complete` or a filter inside a CASE) have no form in it and score 0 above. The 69 it expresses, for every
system:

| system | quality on the 69 | LLM calls | cost | latency (69 q) | median question |
|---|---|---|---|---|---|
| LOTUS | 0.727 | 56,319 | $3.22 | 3,183 s | 35.2 s |
| BlendSQL | 0.726 | 48,449 | $3.98 | 3,908 s | 16.3 s |
| Palimpzest | 0.709 | 57,937 | $7.57 | 4,117 s | 41.8 s |
| ThalamusDB | 0.700 | 160,525 | $12.91 | 24,729 s | 32.2 s |
| PLOP-DP | 0.641 | 18,021 | $0.60 | 7,381 s | 61.0 s |
| SWAN-AISQL | 0.747 | 14,431 | $1.55 | 1,289 s | 10.1 s |

Notes.

- Latency is the wall-clock of a question from the system's start to its last row. The model's response
  times in it are the recorded ones, which the proxy replays request by request; the rest is the system's own
  work on this machine. ThalamusDB's row is over the 69 questions it ran.
- LOTUS runs with its own response cache on (`enable_cache`, sized to hold a whole question) and emptied
  before each question, so one question's answers never serve another. The cache served no request in this
  run: calls, cost and answers equal a run with it off (`LOTUS_CACHE=0`) on all 120 questions.
- One PLOP question (formula_1-02) fails in its optimizer and one ThalamusDB question (california_schools-10)
  in its query rewriting; both score 0.
- ThalamusDB stops a `LIMIT k` query once k rows are certain. That is any k passers, right for an unordered
  LIMIT and a heuristic under `ORDER BY` (formula_1-22 returned a lower-speed Vettel row after one batch).
  The runner now evaluates every row under an ordered LIMIT and orders the complete result; the two such
  questions (formula_1-22, european_football_2-05) were re-recorded that way, at 840 and 1,836 calls.
- The model's verdicts vary between fresh runs at temperature 0, and a single changed verdict moves a
  single-answer question: five fresh recordings of SWAN-AISQL scored 0.775, 0.757, 0.763, 0.755 and 0.777; BlendSQL's
  session recording scored 0.773 and its fresh one 0.761; Palimpzest's 0.769 and 0.766. Read the quality
  column as parity among LOTUS, BlendSQL, Palimpzest and SWAN-AISQL; calls, cost and latency separate them.
- A replay reproduces LOTUS and BlendSQL exactly, and SWAN-AISQL's answers on all 120 questions (its call
  count moves by a fraction of a percent between runs: some questions stop early at a point that depends on the
  order responses arrive in). PLOP, ThalamusDB and Palimpzest vary slightly between runs, so a rerun of any
  system but LOTUS and BlendSQL can reach a prompt that was never recorded; with no provider reachable that
  request fails and the run reports it. The runs in this folder had no such request. (SWAN-AISQL's README
  explains the replay protocol.)
