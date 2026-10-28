"""The meter counts streamed and plain chat completions, and Responses API calls, the same way."""

import json
import threading
import time
import urllib.request

from swan_bench.meter import Meter


def test_streamed_usage_is_found_in_the_last_chunk():
    body = (b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
            b'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":10}}\n\ndata: [DONE]\n\n')
    assert Meter.usage_of(body) == {"prompt_tokens": 100, "completion_tokens": 10}
    assert Meter.usage_of(json.dumps({"usage": {"prompt_tokens": 3}}).encode()) == {"prompt_tokens": 3}


def test_price_uses_cached_input_rate():
    usage = {"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 500}, "completion_tokens": 100}
    assert abs(Meter.price("openai/gpt-5.6-luna", usage) - (500 * 2e-07 + 500 * 2e-08 + 100 * 1.2e-06)) < 1e-12
    assert Meter.price("unknown-model", usage) == 0.0


def test_stub_meter_answers_and_counts():
    with Meter(None) as meter:
        req = urllib.request.Request(meter.url + "/v1/chat/completions", method="POST",
                                     data=json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode(),
                                     headers={"Content-Type": "application/json"})
        answer = json.load(urllib.request.urlopen(req))["choices"][0]["message"]["content"]
        assert answer in ("True", "False")
        assert meter.snapshot()["requests"] == 1
        meter.reset()
        assert meter.snapshot()["requests"] == 0


RESPONSES_USAGE = {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 500}, "output_tokens": 100,
                   "output_tokens_details": {"reasoning_tokens": 40}, "total_tokens": 1100}


def test_responses_usage_is_folded_into_chat_counters():
    usage = Meter.usage_of(json.dumps({"object": "response", "usage": RESPONSES_USAGE}).encode())
    assert usage["prompt_tokens"] == 1000 and usage["completion_tokens"] == 100
    assert usage["prompt_tokens_details"]["cached_tokens"] == 500
    assert usage["completion_tokens_details"]["reasoning_tokens"] == 40
    # priced like the same chat completion
    chat = {"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 500}, "completion_tokens": 100}
    assert Meter.price("gpt-5.6-luna", usage) == Meter.price("gpt-5.6-luna", chat)


def test_streamed_responses_usage_comes_from_the_completed_event():
    body = (b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"x"}\n\n'
            b'event: response.completed\ndata: ' + json.dumps({"type": "response.completed",
                                                              "response": {"usage": RESPONSES_USAGE}}).encode()
            + b"\n\n")
    assert Meter.usage_of(body)["prompt_tokens"] == 1000


def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, method="POST", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def test_stub_meter_answers_responses_requests_and_counts_them():
    body = {"model": "m", "instructions": "answer", "input": [{"role": "user", "content": "hi"}], "store": False}
    with Meter(None) as meter:
        answer = _post(meter.url + "/v1/responses", body)
        assert answer["object"] == "response"
        assert answer["output"][0]["content"][0]["text"] in ("True", "False")
        assert meter.snapshot()["requests"] == 1
        # the stub answers a Responses request exactly as it answers the same chat completion
        chat = {"model": "m", "messages": [{"role": "system", "content": "answer"}, {"role": "user", "content": "hi"}]}
        assert _post(meter.url + "/v1/chat/completions", chat)["choices"][0]["message"]["content"] == \
            answer["output"][0]["content"][0]["text"]
        # a forwarded Responses call is metered like a forwarded chat completion
        with Meter(None) as upstream, Meter(upstream.url) as forwarder:
            _post(forwarder.url + "/v1/responses", body)
            _post(forwarder.url + "/v1/chat/completions", chat)
            assert forwarder.snapshot()["requests"] == 2 and forwarder.snapshot()["errors"] == 0


def test_snapshot_waits_for_a_request_still_in_flight():
    # a system can return its rows while one of its requests is still being answered (a hedge, an asynchronous
    # straggler): the snapshot taken right then must still carry that request, not the next question's
    def slow(body):
        time.sleep(0.5)
        return "True"

    with Meter(None, responder=slow) as meter:
        req = urllib.request.Request(meter.url + "/v1/chat/completions", method="POST",
                                     data=json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]}).encode(),
                                     headers={"Content-Type": "application/json"})
        t = threading.Thread(target=lambda: urllib.request.urlopen(req).read())
        t.start()
        time.sleep(0.1)  # the request is in flight
        assert meter.snapshot()["requests"] == 1
        t.join()
        meter.reset()
        assert meter.snapshot()["requests"] == 0
