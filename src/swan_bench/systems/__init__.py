"""The systems under test. Each runs the question's AISQL query on the MASKED database and returns rows:
SWAN-AISQL as written, BlendSQL, LOTUS, PLOP, ThalamusDB and Palimpzest through their mechanical translations.

A system is a class with a `name` (its directory under `runs/`), a constructor taking the model name, the
OpenAI-compatible endpoint the runner hands it (the meter) and keyword options (`api_key`, `concurrency`,
and the system's own), and `execute(question, query) -> list[tuple]`. Optionally `close()` and
`last_usage() -> dict` (the system's own accounting of the query it just ran).
"""

SYSTEMS = ("blendsql", "lotus", "aisql", "plop", "thalamusdb", "palimpzest")


def load_system(name: str):
    if name == "blendsql":
        from .blendsql import BlendSQLSystem
        return BlendSQLSystem
    if name == "lotus":
        from .lotus import LotusSystem
        return LotusSystem
    if name == "aisql":
        from .aisql import AISQLSystem
        return AISQLSystem
    if name == "plop":
        from .plop import PLOPSystem
        return PLOPSystem
    if name == "thalamusdb":
        from .thalamusdb import ThalamusDBSystem
        return ThalamusDBSystem
    if name == "palimpzest":
        from .palimpzest import PalimpzestSystem
        return PalimpzestSystem
    raise ValueError(f"unknown system {name!r}; expected one of {', '.join(SYSTEMS)}")
