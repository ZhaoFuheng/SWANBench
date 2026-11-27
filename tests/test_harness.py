"""The run harness: a run directory holds the answers of one configuration."""

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from swan_bench import harness, paths
from swan_bench.data import load_questions
from swan_bench.harness import other_configuration

DB = "superhero"


class _Silent:
    """A system that returns no rows; `stop_at` makes its n-th question end the way Ctrl-C would."""

    name = "silent"
    stop_at = None

    def __init__(self, model, endpoint, **options):
        self.asked = 0

    def execute(self, question, query):
        self.asked += 1
        if self.asked == self.stop_at:
            raise KeyboardInterrupt
        return []


class _Deaf(_Silent):
    """Asks the model once per question and returns no rows whatever comes back, as a system does that puts a
    default of its own where a model answer is missing."""

    name = "deaf"

    def __init__(self, model, endpoint, **options):
        super().__init__(model, endpoint)
        self.endpoint = endpoint

    def execute(self, question, query):
        request = urllib.request.Request(
            self.endpoint + "/v1/chat/completions", method="POST", headers={"Content-Type": "application/json"},
            data=json.dumps({"model": "m", "messages": [{"role": "user", "content": question.qid}]}).encode())
        try:
            urllib.request.urlopen(request)
        except urllib.error.HTTPError:
            pass
        return []


class _Twice(_Deaf):
    """Asks the same thing for two rows of a question, as a system without deduplication does."""

    def execute(self, question, query):
        super().execute(question, query)
        return super().execute(question, query)


@pytest.fixture
def answers_each_text_once():
    """A model endpoint that answers the first request for a text and fails every later one with the same text."""
    seen, lock = set(), threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            with lock:
                first = body not in seen
                seen.add(body)
            reply = json.dumps({"choices": [{"message": {"role": "assistant", "content": "true"}}], "usage": {}}
                               if first else {"error": {"message": "down"}}).encode()
            self.send_response(200 if first else 500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def run(tmp_path, monkeypatch):
    """harness.run on the first two superhero questions with the silent system, under a temporary runs/ folder."""
    if not paths.database_path(DB).is_file():
        pytest.skip("run `swan-bench prepare` first")
    monkeypatch.setattr(paths, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    system = {"class": _Silent}
    monkeypatch.setattr(harness, "load_system", lambda name: system["class"])
    monkeypatch.setattr(_Silent, "stop_at", None)
    qids = {q.qid for q in load_questions(DB)[:2]}

    def start(model="model-a", endpoint=None, **options):
        return harness.run("silent", model, endpoint, databases=[DB], qids=qids, tag="t",
                           **{"concurrency": 20, **options})

    start.system = system

    start.dir = tmp_path / "runs" / "silent" / "t"
    return start


def _records(run):
    return (run.dir / "queries.jsonl").read_text()


def test_a_run_of_the_same_configuration_resumes(run):
    assert run()["overall"]["total"] == 2
    before = _records(run)
    assert run()["overall"]["total"] == 2 and _records(run) == before  # nothing left to run, nothing rewritten
    assert run(sema_bin="/somewhere/else/duckdb")["overall"]["total"] == 2  # where a binary is does not matter


@pytest.mark.parametrize("change", [{"model": "model-b"}, {"concurrency": 10}, {"batch": 4}],
                         ids=["model", "concurrency", "an option the first run did not have"])
def test_a_run_of_another_configuration_stops_before_it_runs_anything(run, change):
    run()
    before, scores = _records(run), (run.dir / "scores.json").read_text()
    with pytest.raises(SystemExit, match="holds answers recorded with"):
        run(**change)
    assert _records(run) == before and (run.dir / "scores.json").read_text() == scores


def test_rerun_starts_the_directory_over_with_the_new_configuration(run):
    run()
    report = run(model="model-b", rerun=True)
    assert report["model"] == "model-b" and report["overall"]["total"] == 2
    assert json.loads((run.dir / "scores.json").read_text())["model"] == "model-b"


def test_an_interrupted_run_keeps_its_configuration(run, monkeypatch):
    monkeypatch.setattr(_Silent, "stop_at", 2)
    with pytest.raises(KeyboardInterrupt):
        run()
    assert len(_records(run).splitlines()) == 1  # one answer is on disk, and so is how it was obtained
    assert json.loads((run.dir / "scores.json").read_text())["model"] == "model-a"
    monkeypatch.setattr(_Silent, "stop_at", None)
    with pytest.raises(SystemExit, match="model model-a"):
        run(model="model-b")
    assert run()["overall"]["total"] == 2  # the same configuration finishes it


@pytest.mark.parametrize("fills_in_defaults", [True, False])
def test_an_unanswered_request_is_an_error_for_a_system_that_fills_in_a_default(run, monkeypatch, fills_in_defaults):
    monkeypatch.setattr(_Deaf, "answers_failed_calls", fills_in_defaults, raising=False)
    run.system["class"] = _Deaf
    report = run(endpoint="http://127.0.0.1:9")  # nothing listens there: every model request fails
    records = [json.loads(line) for line in _records(run).splitlines()]
    assert all(r["requests"] == 1 and r["errors"] == 1 and r["unanswered"] == 1 for r in records)
    if fills_in_defaults:
        assert all("never got an answer" in r["error"] and r["quality"] == 0.0 and not r["match"] for r in records)
        assert report["overall"]["failed"] == 2
    else:  # a system that raises on a failed call has already reported it
        assert all(r["error"] is None for r in records)
    assert report["overall"]["unanswered"] == 2


def test_a_failed_request_is_an_error_even_when_another_row_got_the_same_text_answered(run, monkeypatch,
                                                                                     answers_each_text_once):
    monkeypatch.setattr(_Twice, "answers_failed_calls", True, raising=False)
    run.system["class"] = _Twice
    report = run(endpoint=answers_each_text_once)
    records = [json.loads(line) for line in _records(run).splitlines()]
    # one row's request was answered and the other's failed: no request text is left without an answer, yet a row is
    assert all((r["requests"], r["errors"], r["unanswered"]) == (2, 1, 0) for r in records)
    assert all("1 model request(s) failed" in r["error"] and r["quality"] == 0.0 for r in records)
    assert report["overall"]["failed"] == 2 and report["overall"]["unanswered"] == 0


def test_configurations_are_compared_on_what_a_file_holds():
    meta = {"model": "m", "options": {"concurrency": 20, "sema_bin": "/a", "settings": {"ai_concurrency": 20}}}
    assert other_configuration({}, meta) is None  # an older run directory that does not say
    assert other_configuration({"model": "m"}, meta) is None
    assert other_configuration(json.loads(json.dumps(meta)), meta) is None
    assert other_configuration({"model": "m", "options": {**meta["options"], "sema_bin": "/b", "batch": None}}, meta) is None
    assert "batch 4" in other_configuration({"model": "m", "options": {**meta["options"], "batch": 4}}, meta)
    assert "settings" in other_configuration(
        {"model": "m", "options": {**meta["options"], "settings": {"ai_concurrency": 20, "ai_pullup": "false"}}}, meta)
    assert "model other" in other_configuration({"model": "other", "options": meta["options"]}, meta)
