"""The Palimpzest system hands each AI call to its server as one semantic-operator request and reads the answers back."""

import json

import pytest

from swan_bench.aisql import AICall
from swan_bench.systems import load_system
from swan_bench.systems.palimpzest import PalimpzestOps, PalimpzestSystem


class FakeProc:
    """Stands in for the Palimpzest server: records requests, answers from a script."""

    def __init__(self, answers):
        self.requests, self.answers = [], answers
        self.stdin, self.stdout = self, self

    def write(self, line):
        self.requests.append(json.loads(line))

    def flush(self):
        pass

    def readline(self):
        return json.dumps(self.answers[len(self.requests) - 1]) + "\n"

    def close(self):
        pass

    def wait(self, timeout=None):
        pass


def _ops(answers):
    ops = PalimpzestOps.__new__(PalimpzestOps)
    ops.proc = FakeProc(answers)
    return ops


def test_registered():
    assert load_system("palimpzest") is PalimpzestSystem


def test_filter_request_and_answers():
    ops = _ops([{"answers": [True, False]}])
    call = AICall(node=None, fn="ai_filter", question="Is it big?", name="name")
    assert ops.filter(call, ["a", "b"]) == [True, False]
    assert ops.proc.requests == [{"op": "filter", "name": "name", "question": "Is it big?", "values": ["a", "b"]}]


def test_classify_carries_labels_and_complete_the_suffix():
    ops = _ops([{"answers": ["red"]}, {"answers": ["7"]}])
    call = AICall(node=None, fn="ai_classify", question="Which colour?", name="name")
    assert ops.classify(call, ["x"], ["red", "blue"]) == ["red"]
    assert ops.proc.requests[0]["labels"] == ["red", "blue"]
    call = AICall(node=None, fn="ai_complete", question="How many?", name="name", suffix=" Answer with the number only.")
    assert ops.complete(call, ["x"]) == ["7"]
    assert ops.proc.requests[1]["suffix"] == " Answer with the number only."


def test_server_error_becomes_the_query_error():
    ops = _ops([{"error": "ValueError: boom"}])
    call = AICall(node=None, fn="ai_filter", question="Q?", name="name")
    with pytest.raises(RuntimeError, match="boom"):
        ops.filter(call, ["x"])
