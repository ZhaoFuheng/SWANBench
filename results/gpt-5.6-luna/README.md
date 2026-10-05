# SWAN 2.0 on gpt-5.6-luna (2026-10-03)

These results are replays of the published LLM cache. The four systems were recorded fresh, one after the
other, in a single session on 2026-10-03: SWAN-AISQL (with `ai_filter` stating a one-sentence reason before
its verdict), BlendSQL 0.1.27 zero-shot, LOTUS 1.2.4, then PLOP (Morrila, DP cost-model mode, on the authors'
fork), 20 requests in flight each, every call through the SWAN-AISQL cache proxy with an empty recording
cache; ThalamusDB (0.1.15, exact mode) was recorded the next night, 2026-10-03/04, and Palimpzest (1.5.3,
Abacus optimizer, MaxQuality) on 2026-10-04, through the same proxy, and their calls joined the same cache. That recording is published with SWAN-AISQL (its `serve/fetch_cache.sh`
downloads it). Model
inference at temperature 0 is treated as deterministic, so the cache is the single source of answers and
latencies: each system was then run again from the published cache with the recorded latencies replayed,
and those runs are the files in this folder. Quality, calls, cost and latency therefore come from one
recording and are comparable across systems.

| database | SWAN-AISQL | BlendSQL | LOTUS | PLOP-DP | ThalamusDB | Palimpzest |
|---|---|---|---|---|---|---|
| california_schools | 0.700 | 0.777 | 0.733 | 0.679 | 0.292 | 0.768 |
| superhero | 0.503 | 0.488 | 0.507 | 0.508 | 0.295 | 0.512 |
| formula_1 | 0.995 | 0.975 | 1.000 | 0.902 | 0.499 | 0.993 |
| european_football_2 | 0.831 | 0.852 | 0.801 | 0.671 | 0.498 | 0.805 |
| **quality (mean)** | **0.757** | **0.773** | **0.760** | **0.690** | **0.396** | **0.769** |
| exact match | 60/120 | 57/120 | 59/120 | 51/120 | 27/120 | 60/120 |
| LLM calls | 22,340 | 59,564 | 69,204 | 25,604 | 158,290 | 70,645 |
| cost | $2.38 | $5.00 | $4.04 | ~$1.72 | ~$12.73 | $9.76 |
| latency, all 120 questions | 2,153 s | 4,359 s | 4,101 s | 10,431 s | 36,945 s (69 q) | 2,799 s† |
| latency, median question | 6.5 s | 8.7 s | 23.5 s | 40.9 s | 30.4 s (69 q) | 17.6 s |
| fastest on | 39 questions | 48 | 12 | 3 | 9 (of its 69) | 9 |

ThalamusDB's dialect has boolean WHERE-clause filters only, so 51 of the 120 questions (those with
`ai_classify`, `ai_complete` or a filter inside a CASE) have no form in it and score 0 above; its mean over
the 69 questions it expresses is **0.689** (27 exact). The same 69 questions for every system:

| system | quality on the 69 | exact | LLM calls | cost | latency (69 q) | median question |
|---|---|---|---|---|---|---|
| SWAN-AISQL | 0.742 | 31/69 | 17,866 | $1.90 | 1,699 s | 15.4 s |
| BlendSQL | 0.726 | 25/69 | 48,393 | $4.04 | 3,475 s | 12.9 s |
| LOTUS | 0.727 | 31/69 | 56,319 | $3.22 | 3,231 s | 35.5 s |
| PLOP-DP | 0.630 | 22/69 | 17,994 | ~$0.92 | 7,580 s | 61.3 s |
| ThalamusDB | 0.689 | 27/69 | 158,290 | ~$12.73 | 36,945 s | 30.4 s |
| Palimpzest | 0.725 | 30/69 | 57,677 | $7.51 | 2,007 s | 23.6 s |

Latency is the wall-clock time of a question from the system's start to its last row, model response times
included (`seconds` per question in `answers.jsonl`, summed per database in `scores.json`). BlendSQL's total
includes one question (european_football_2-22) that took 1,907 s at 27,258 calls; without it its total is
2,452 s. †Palimpzest's row is its live run of 2026-10-04 (why its replay is not used is explained below); its
latency was recorded on a different day from the 2026-10-03 session, and provider load differs between
days, so compare it with the others' latencies with that in mind. ThalamusDB's cost is an estimate from its
token counts; one of its 69 questions
(california_schools-10) fails inside its query rewriting and scores 0. SWAN-AISQL's `requests` in
`scores.json` (22,655) include 315 attempts at 45 prompts (0.2%, on 4
questions) that the recording does not hold: which rows the engine asks about first depends on run-time
ordering, those prompts fail without a provider key, and no answer depends on them (nine replays with the
provider reachable gave identical answers); the table counts the 22,340 calls the cache served. PLOP's cost
is an estimate from its token counts (its requests carry no provider cost header); one question
(formula_1-02, an AI filter inside an `IN` subquery) fails in its optimizer and scores 0. Quality and exact
match are defined in the repository README. Per system, `answers.jsonl` holds each question's answer, calls,
tokens, cost and seconds, and `scores.json` the totals per database.

Reproducibility: a replay of the published cache returns these answers for SWAN-AISQL, BlendSQL and LOTUS
exactly (three LOTUS replays agreed on all 120 answers). PLOP returns a different set of k rows on the any-k
questions (`LIMIT k` without `ORDER BY`) from the same verdicts between runs, so its mean varies between
0.690 and 0.704 across our replays. Palimpzest's row is its live run: which prompts it issues is not the same
from run to run (26 questions moved by a few calls between two runs), so a replay from the cache issued 2,148
requests the recording does not hold (three european_football_2 questions) and scored 0.763 at 72,062
calls, and its replayed latency (5,054 s) does not reproduce the live wall-clock (2,799 s). ThalamusDB's row
is likewise its live run: it samples which rows to judge next, so on the five european_football_2 questions
its caps cut short, a replay asked about rows the recording does not hold and scored 0 (replay: 0.623 on
the 69, 147,510 calls, 36,618 s). The live session's own answers differed from the replay for LOTUS on 9
questions (live: 0.751, 56 exact) and for PLOP on its any-k questions (live: 0.703); SWAN-AISQL's and
BlendSQL's were identical. A run that asks the provider afresh is a different experiment: the systems'
earlier, separate recordings scored 0.775 (SWAN-AISQL), 0.741 (BlendSQL), 0.770 (LOTUS) and 0.739 (PLOP),
because gpt-5.6-luna does not answer every repeated prompt identically and a single changed verdict moves a
single-answer question. Read the quality column as parity among SWAN-AISQL, BlendSQL and LOTUS; calls, cost
and latency are the separation.
