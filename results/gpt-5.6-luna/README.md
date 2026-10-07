# SWAN 2.0 on gpt-5.6-luna

Every system was recorded fresh through an empty cache, 20 requests in flight, into the published LLM cache
(fetched by SWAN-AISQL's `serve/fetch_cache.sh`), and the files in this folder are those runs, so quality,
calls, cost and latency come from one recording and reproduce from it. LOTUS 1.2.4 and PLOP (Morrila, DP
cost-model mode) were recorded in one session on 2026-10-03; ThalamusDB 0.1.15 (exact mode) on
2026-10-03/04; BlendSQL 0.1.27 and Palimpzest 1.5.3 (Abacus optimizer) on 2026-10-05; SWAN-AISQL on 2026-10-06. Quality is defined in the repository README. Per system,
`answers.jsonl` holds each question's answer, calls, tokens, cost and seconds, and `scores.json` the totals
per database. The cache version on Zenodo today (the first, from 2026-10-02) predates these recordings; the next
version, which holds them, is being prepared, and until then a replay of this folder needs a provider key.

| database | LOTUS | BlendSQL | Palimpzest | ThalamusDB | PLOP-DP | SWAN-AISQL |
|---|---|---|---|---|---|---|
| california_schools | 0.733 | 0.717 | 0.717 | 0.292 | 0.679 | 0.740 |
| superhero | 0.507 | 0.498 | 0.529 | 0.295 | 0.508 | 0.467 |
| formula_1 | 1.000 | 0.984 | 0.968 | 0.499 | 0.902 | 0.988 |
| european_football_2 | 0.801 | 0.845 | 0.850 | 0.498 | 0.671 | 0.825 |
| **quality (mean)** | **0.760** | **0.761** | **0.766** | **0.396** | **0.690** | **0.755** |
| LLM calls | 69,204 | 59,620 | 70,933 | 158,290 | 25,604 | 19,695 |
| cost | $4.04 | $4.95 | $9.85 | $12.73 | $1.72 | $2.13 |
| latency, all 120 questions | 4,101 s | 4,608 s | 2,991 s | 36,945 s (69 q) | 10,431 s | 2,055 s |

ThalamusDB's dialect has boolean WHERE-clause filters only, so 51 questions (those with `ai_classify`,
`ai_complete` or a filter inside a CASE) have no form in it and score 0 above. The 69 it expresses, for every
system:

| system | quality on the 69 | LLM calls | cost | latency (69 q) | median question |
|---|---|---|---|---|---|
| LOTUS | 0.727 | 56,319 | $3.22 | 3,231 s | 35.5 s |
| BlendSQL | 0.726 | 48,449 | $3.98 | 3,663 s | 13.3 s |
| Palimpzest | 0.703 | 57,943 | $7.60 | 2,161 s | 24.1 s |
| ThalamusDB | 0.689 | 158,290 | $12.73 | 36,945 s | 30.4 s |
| PLOP-DP | 0.630 | 17,994 | $0.92 | 7,580 s | 61.3 s |
| SWAN-AISQL | 0.742 | 15,347 | $1.67 | 1,590 s | 12.8 s |

Notes.

- Latency is the wall-clock of a question from the system's start to its last row, model response times
  included. ThalamusDB's row is over the 69 questions it ran.
- PLOP's and ThalamusDB's costs are computed from their token counts at list price. One PLOP question
  (formula_1-02) fails in its optimizer and one ThalamusDB question (california_schools-10) in its query
  rewriting; both score 0.
- The model's verdicts vary between fresh runs at temperature 0, and a single changed verdict moves a
  single-answer question: four fresh recordings of SWAN-AISQL scored 0.775, 0.757, 0.763 and 0.755; BlendSQL's
  session recording scored 0.773 and its fresh one 0.761; Palimpzest's 0.769 and 0.766. Read the quality
  column as parity among LOTUS, BlendSQL, Palimpzest and SWAN-AISQL; calls, cost and latency separate them.
- Replaying the cache reproduces SWAN-AISQL, BlendSQL and LOTUS exactly; PLOP, ThalamusDB and Palimpzest
  vary slightly between runs, so an exact rerun of those needs a provider key (SWAN-AISQL's README explains
  the replay protocol).
