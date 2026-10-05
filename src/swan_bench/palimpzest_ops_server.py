"""Palimpzest as a semantic-operator service for the SWAN 2.0 harness. Runs in Palimpzest's own interpreter
(PALIMPZEST_PYTHON; see scripts/setup_palimpzest.sh), so it imports nothing from swan_bench.

    python palimpzest_ops_server.py MODEL ENDPOINT DOP [--optimizer pareto|none] [--policy MaxQuality|MinCost]

One JSON request per stdin line, one JSON response per stdout line. A request is one semantic operator over
one context column, the unit `lotus_exec.py` hands to a system (the AISQL query's written order decides when
each runs):

  {"op": "filter",   "name": <column>, "question": <text>, "values": [...]}            -> {"answers": [bool, ...]}
  {"op": "complete", "name": <column>, "question": <text>, "suffix": <text>, "values": [...]} -> {"answers": [str|None, ...]}
  {"op": "classify", "name": <column>, "question": <text>, "labels": [...], "values": [...]}  -> {"answers": [str|None, ...]}
  {"op": "agg",      "name": <column>, "instruction": <text>, "values": [...]}          -> {"answers": [str]}

Each request becomes a Palimpzest program over a MemoryDataset of the values (plus a row index `swan_i`,
since Palimpzest's schemas reject leading underscores) (sem_filter / sem_add_columns /
sem_agg, the context column named as in the query) run with Abacus's pareto plan search (`optimizer_strategy=
"pareto"`; its sample-based cost estimation needs a validator or training set, which the benchmark does not
supply, so the default cost model decides and no sampling calls are made), MaxQuality, parallel execution,
DOP workers and one model: the benchmark model through the harness's endpoint (`Model(model_id,
api_base=...)`). Palimpzest's own output goes to stderr.
"""
import json
import sys


def main() -> None:
    model, endpoint, dop = sys.argv[1], sys.argv[2], int(sys.argv[3])
    flags = sys.argv[4:]
    optimizer = flags[flags.index("--optimizer") + 1] if "--optimizer" in flags else "pareto"
    policy_name = flags[flags.index("--policy") + 1] if "--policy" in flags else "MaxQuality"
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    import palimpzest as pz
    import pandas as pd
    from palimpzest.constants import Model

    lm_model = model if "/" in model else f"openai/{model}"
    m = Model(lm_model, api_base=endpoint.rstrip("/") + "/v1")

    def config():
        policy = pz.MaxQuality() if policy_name == "MaxQuality" else pz.MinCost()
        return pz.QueryProcessorConfig(policy=policy, optimizer_strategy=optimizer, execution_strategy="parallel",
                                       max_workers=dop, join_parallelism=dop, available_models=[m],
                                       progress=False, verbose=False)

    n = 0

    def handle(req: dict) -> dict:
        nonlocal n
        n += 1
        op, name, values = req["op"], req["name"], req["values"]
        df = pd.DataFrame({"swan_i": list(range(len(values))), name: values})
        ds = pz.MemoryDataset(id=f"swan_{op}_{n}", vals=df)
        if op == "filter":
            ds = ds.sem_filter(req["question"], depends_on=[name]).project(["swan_i"])
            out = ds.run(config()).to_df()
            passed = {int(i) for i in out["swan_i"]} if len(out) else set()
            return {"answers": [i in passed for i in range(len(values))]}
        if op == "agg":
            ds = ds.sem_agg(col={"name": "answer", "type": str, "description": req["instruction"]},
                            agg=req["instruction"], depends_on=[name])
            out = ds.run(config()).to_df()
            return {"answers": [None if not len(out) else str(out["answer"].iloc[0]).strip()]}
        if op == "complete":
            desc = req["question"] + req.get("suffix", "")
        else:  # classify
            desc = req["question"] + " Answer with exactly one of: " + ", ".join(req["labels"]) + "."
        ds = ds.sem_add_columns([{"name": "answer", "type": str, "desc": desc}], depends_on=[name]).project(["swan_i", "answer"])
        out = ds.run(config()).to_df()
        got = {int(r["swan_i"]): (None if r["answer"] is None else str(r["answer"]).strip()) for _, r in out.iterrows()} if len(out) else {}
        return {"answers": [got.get(i) for i in range(len(values))]}

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except Exception as ex:  # noqa: BLE001 -- the client reports the failure as the query's error
            resp = {"error": f"{type(ex).__name__}: {ex}"[:1000]}
        real_stdout.write(json.dumps(resp) + "\n")
        real_stdout.flush()


if __name__ == "__main__":
    main()
