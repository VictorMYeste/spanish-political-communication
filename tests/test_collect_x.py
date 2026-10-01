import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


collect = load_module("collect_x", ROOT / "src" / "collect_x.py")


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status_code, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    """Returns queued responses and records the parameters of each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        return self.responses.pop(0)


def make_client(tmp_path, responses, monkeypatch):
    monkeypatch.setattr(collect.time, "sleep", lambda seconds: None)
    client = collect.Client("token", tmp_path / "manifest.jsonl")
    client.session = FakeSession(responses)
    return client


def test_month_slices_covers_partial_months():
    slices = collect.month_slices(utc("2022-04-10T00:00:00"), utc("2022-06-15T00:00:00"))
    assert [label for label, _, _ in slices] == ["2022-04", "2022-05", "2022-06"]
    assert slices[0][1] == utc("2022-04-10T00:00:00")
    assert slices[0][2] == utc("2022-05-01T00:00:00")
    assert slices[-1][2] == utc("2022-06-15T00:00:00")


def test_month_slices_crosses_year_end():
    slices = collect.month_slices(utc("2022-12-01T00:00:00"), utc("2023-02-01T00:00:00"))
    assert [label for label, _, _ in slices] == ["2022-12", "2023-01"]


def test_query_and_timestamp_formats():
    assert collect.build_query("from:{x_user_id} -is:retweet", "123") == "from:123 -is:retweet"
    assert collect.api_timestamp(utc("2023-08-17T05:06:07")) == "2023-08-17T05:06:07Z"
    assert collect.parse_day("2023-08-17") == utc("2023-08-17T00:00:00")


def test_manifest_round_trip(tmp_path):
    path = tmp_path / "manifest.jsonl"
    collect.append_manifest(path, {"type": "request", "actor_id": "a", "month": "2024-01"})
    collect.append_manifest(path, {"type": "month_complete", "actor_id": "a", "month": "2024-01"})
    collect.append_manifest(path, {"type": "month_complete", "actor_id": "b", "month": "2024-02"})
    completed = collect.load_manifest(path)
    assert set(completed) == {("a", "2024-01"), ("b", "2024-02")}
    assert collect.load_manifest(tmp_path / "missing.jsonl") == {}


def test_count_month_adds_up_pages(tmp_path, monkeypatch):
    responses = [
        FakeResponse(200, {"meta": {"total_tweet_count": 40, "next_token": "t1"}}),
        FakeResponse(200, {"meta": {"total_tweet_count": 2}}),
    ]
    client = make_client(tmp_path, responses, monkeypatch)
    total = collect.count_month(
        client, "from:1 -is:retweet", utc("2024-01-01T00:00:00"), utc("2024-02-01T00:00:00"), {}
    )
    assert total == 42
    assert client.session.calls[1]["params"]["next_token"] == "t1"
    assert client.session.calls[0]["params"]["granularity"] == "day"


def test_collect_month_writes_pages_and_follows_tokens(tmp_path, monkeypatch):
    responses = [
        FakeResponse(200, {"data": [{"id": "1"}, {"id": "2"}], "includes": {"tweets": [{"id": "9"}]}, "meta": {"next_token": "t1"}}),
        FakeResponse(200, {"data": [{"id": "3"}], "meta": {}}),
    ]
    client = make_client(tmp_path, responses, monkeypatch)
    out_file = tmp_path / "raw" / "2024-01.jsonl"
    settings = {"max_results": 500, "tweet_fields": ["created_at", "lang"], "expand_referenced_posts": True}
    posts, referenced, pages = collect.collect_month(
        client, "from:1 -is:retweet", utc("2024-01-01T00:00:00"), utc("2024-02-01T00:00:00"), settings, out_file, {}
    )
    assert (posts, referenced, pages) == (3, 1, 2)
    lines = out_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["data"][0]["id"] == "1"
    first = client.session.calls[0]["params"]
    assert first["tweet.fields"] == "created_at,lang"
    assert first["expansions"] == "referenced_tweets.id,author_id"
    assert first["max_results"] == "500"


def test_collect_month_rewrites_file_when_run_again(tmp_path, monkeypatch):
    out_file = tmp_path / "raw" / "2024-01.jsonl"
    out_file.parent.mkdir(parents=True)
    out_file.write_text("stale\n", encoding="utf-8")
    client = make_client(tmp_path, [FakeResponse(200, {"data": [{"id": "1"}], "meta": {}})], monkeypatch)
    collect.collect_month(
        client, "q", utc("2024-01-01T00:00:00"), utc("2024-02-01T00:00:00"), {"tweet_fields": []}, out_file, {}
    )
    assert "stale" not in out_file.read_text(encoding="utf-8")


def test_cost_counts_posts_referenced_posts_and_counts_requests(tmp_path, monkeypatch):
    responses = [
        FakeResponse(200, {"meta": {"total_tweet_count": 10}}),
        FakeResponse(200, {"data": [{"id": "1"}, {"id": "2"}], "includes": {"tweets": [{"id": "9"}]}}),
    ]
    client = make_client(tmp_path, responses, monkeypatch)
    client.get("/tweets/counts/all", {}, {})
    client.get("/tweets/search/all", {}, {})
    assert client.counts_requests == 1
    assert client.posts_read == 3
    assert client.cost() == pytest.approx(0.010 + 3 * 0.005)
    assert client.over_budget() is False


def test_collect_month_stops_when_over_budget(tmp_path, monkeypatch):
    responses = [
        FakeResponse(200, {"data": [{"id": "1"}, {"id": "2"}], "meta": {"next_token": "t1"}}),
        FakeResponse(200, {"data": [{"id": "3"}], "meta": {"next_token": "t2"}}),
    ]
    client = make_client(tmp_path, responses, monkeypatch)
    client.max_cost = 0.011  # covers two posts, not three
    out_file = tmp_path / "raw" / "2024-01.jsonl"
    with pytest.raises(collect.BudgetExceeded):
        collect.collect_month(
            client, "q", utc("2024-01-01T00:00:00"), utc("2024-02-01T00:00:00"), {"tweet_fields": []}, out_file, {}
        )
    # Pages already paid for are kept, so the data is not lost.
    assert len(out_file.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_access_errors_stop_the_run(tmp_path, monkeypatch):
    payload = {"title": "Client Forbidden", "reason": "client-not-enrolled"}
    client = make_client(tmp_path, [FakeResponse(403, payload)], monkeypatch)
    with pytest.raises(collect.AccessError) as error:
        client.get("/tweets/search/all", {}, {})
    assert "not-enrolled" in str(error.value)


def test_rate_limit_is_retried(tmp_path, monkeypatch):
    responses = [
        FakeResponse(429, {"title": "Too Many Requests"}, {"x-rate-limit-reset": "1"}),
        FakeResponse(200, {"data": []}),
    ]
    client = make_client(tmp_path, responses, monkeypatch)
    assert client.get("/tweets/search/all", {}, {}) == {"data": []}
    assert client.requests_made == 2


def test_server_errors_are_retried_then_raise(tmp_path, monkeypatch):
    responses = [FakeResponse(500, {"title": "Internal Error"}) for _ in range(collect.MAX_RETRIES)]
    client = make_client(tmp_path, responses, monkeypatch)
    with pytest.raises(RuntimeError):
        client.get("/tweets/search/all", {}, {})


def test_requests_are_logged_in_the_manifest(tmp_path, monkeypatch):
    client = make_client(tmp_path, [FakeResponse(200, {"data": [{"id": "1"}]})], monkeypatch)
    client.get("/tweets/search/all", {}, {"actor_id": "pedro_sanchez", "month": "2024-01"})
    record = json.loads((tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert record["type"] == "request"
    assert record["actor_id"] == "pedro_sanchez"
    assert record["status"] == 200
    assert record["results"] == 1
