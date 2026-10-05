#!/bin/bash
# Palimpzest (PyPI `palimpzest`, MIT DSG's semantic-operator system with the Abacus optimizer) pulls in torch
# and sentence-transformers, so it gets its own interpreter: .venv-palimpzest (Python 3.12). On an Intel Mac
# the last torch wheel (2.2) needs numpy < 2, which in turn needs scipy < 1.13.
#
#   scripts/setup_palimpzest.sh
#   uv run swan-bench run --system palimpzest
set -eu
cd "$(dirname "$0")/.."
uv venv --python 3.12 .venv-palimpzest
uv pip install --python .venv-palimpzest/bin/python "palimpzest==1.5.3"
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "x86_64" ]; then
	uv pip install --python .venv-palimpzest/bin/python "numpy<2" "scipy<1.13"
fi
echo "Palimpzest ready in .venv-palimpzest"
