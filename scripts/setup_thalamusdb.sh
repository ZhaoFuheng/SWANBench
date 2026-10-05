#!/bin/bash
# ThalamusDB pins its dependencies (sqlglot 27, pandas 2.3.1, ...) and cannot share the benchmark's
# environment, so it gets its own: .venv-thalamusdb with Python 3.12 and thalamusdb 0.1.15. The
# `thalamusdb` system runs its queries in that interpreter (src/swan_bench/thalamusdb_runner.py).
#
#   scripts/setup_thalamusdb.sh
#   uv run swan-bench run --system thalamusdb --duckdb-bin "$SWAN_AISQL_DUCKDB"
set -eu
cd "$(dirname "$0")/.."
uv venv --python 3.12 .venv-thalamusdb
uv pip install --python .venv-thalamusdb/bin/python "thalamusdb==0.1.15"
echo "ThalamusDB ready in .venv-thalamusdb"
