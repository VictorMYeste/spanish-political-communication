#!/usr/bin/env python3
"""Collect posts written by the target politicians with the X API v2.

The script runs in two phases. It first asks the API how many posts match each
monthly query (full-archive counts, $0.010 per request) and prints the expected
cost. Only after confirmation does it download the posts with full-archive
search ($0.005 per post).

Retweets are excluded by the query, so they are never downloaded or paid for.
Posts referenced by replies and quote posts are requested as expansions, so
that short posts keep their context; each referenced post is an extra read.

Response pages are stored untouched, one JSON page per line, and every request
is logged in a manifest. Months already marked complete are skipped, so an
interrupted run can be resumed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_x_access import (  # noqa: E402
    API_BASE,
    EXPLANATIONS,
    USER_AGENT,
    classify_response,
    load_accounts,
    load_env_file,
)

ROOT = Path(__file__).resolve().parents[1]
PRICE_PER_POST = 0.005
PRICE_PER_COUNTS_REQUEST = 0.010
# Full-archive search allows 1 request per second and 300 per 15 minutes.
MIN_SECONDS_BETWEEN_REQUESTS = 1.1
MAX_RETRIES = 5


class AccessError(RuntimeError):
    """The API refused the request for credentials, enrolment or billing."""


class BudgetExceeded(RuntimeError):
    """The run reached the cost limit given with --max-cost."""


def month_slices(
    start: datetime, end: datetime
) -> List[Tuple[str, datetime, datetime]]:
    """Split a period into (label, start, end) month slices, end exclusive."""
    slices: List[Tuple[str, datetime, datetime]] = []
    cursor = start.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
    )
    while cursor < end:
        if cursor.month == 12:
            following = cursor.replace(year=cursor.year + 1, month=1)
        else:
            following = cursor.replace(month=cursor.month + 1)
        slice_start = max(cursor, start)
        slice_end = min(following, end)
        if slice_start < slice_end:
            slices.append((f"{cursor.year:04d}-{cursor.month:02d}", slice_start, slice_end))
        cursor = following
    return slices


def api_timestamp(moment: datetime) -> str:
    """Format a datetime the way the X API expects it."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_query(template: str, user_id: str) -> str:
    """Build the search query for one account."""
    return template.format(x_user_id=user_id)


def load_manifest(path: Path) -> Dict[Tuple[str, str], Dict[str, object]]:
    """Read the manifest and return the months already marked complete."""
    completed: Dict[Tuple[str, str], Dict[str, object]] = {}
    if not path.exists():
        return completed
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("type") == "month_complete":
            completed[(str(record.get("actor_id")), str(record.get("month")))] = record
    return completed


def append_manifest(path: Path, record: Mapping[str, object]) -> None:
    """Append one record to the manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class Client:
    """Minimal X API client with rate-limit handling and request logging."""

    def __init__(
        self,
        token: str,
        manifest_path: Path,
        timeout: float = 60.0,
        max_cost: Optional[float] = None,
    ):
        self.token = token
        self.manifest_path = manifest_path
        self.timeout = timeout
        self.max_cost = max_cost
        self.session = requests.Session()
        self.last_request = 0.0
        self.requests_made = 0
        self.counts_requests = 0
        self.posts_read = 0

    def _account_for(self, path: str, payload: Mapping[str, object]) -> None:
        """Count the billable resources of a successful response."""
        if path.endswith("/counts/all"):
            self.counts_requests += 1
        elif path.endswith("/search/all"):
            includes = payload.get("includes") or {}
            self.posts_read += len(payload.get("data") or [])
            self.posts_read += len(includes.get("tweets") or [])

    def cost(self) -> float:
        """Approximate dollars spent so far, before same-day deduplication."""
        return (
            self.posts_read * PRICE_PER_POST
            + self.counts_requests * PRICE_PER_COUNTS_REQUEST
        )

    def over_budget(self) -> bool:
        return self.max_cost is not None and self.cost() > self.max_cost

    def _wait_turn(self) -> None:
        elapsed = time.monotonic() - self.last_request
        if self.last_request and elapsed < MIN_SECONDS_BETWEEN_REQUESTS:
            time.sleep(MIN_SECONDS_BETWEEN_REQUESTS - elapsed)

    def get(self, path: str, params: Mapping[str, str], context: Mapping[str, object]) -> Dict:
        """Send a GET request, retrying on rate limits and server errors."""
        for attempt in range(1, MAX_RETRIES + 1):
            self._wait_turn()
            response = self.session.get(
                f"{API_BASE}{path}",
                params=params,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": USER_AGENT,
                },
                timeout=self.timeout,
            )
            self.last_request = time.monotonic()
            self.requests_made += 1
            try:
                payload = response.json()
            except ValueError:
                payload = {"raw_body": response.text[:500]}
            append_manifest(
                self.manifest_path,
                {
                    "type": "request",
                    "path": path,
                    "status": response.status_code,
                    "attempt": attempt,
                    "results": len(payload.get("data", []) or []),
                    "next_token": (payload.get("meta", {}) or {}).get("next_token"),
                    "requested_at_utc": datetime.now(timezone.utc).isoformat(),
                    **context,
                },
            )
            if response.status_code == 200:
                self._account_for(path, payload)
                return payload
            access = classify_response(response.status_code, payload)
            if access in {"invalid-credentials", "not-enrolled", "no-credits", "usage-capped"}:
                raise AccessError(f"{access}: {EXPLANATIONS[access]}")
            if access == "rate-limited":
                reset = response.headers.get("x-rate-limit-reset")
                delay = 60.0
                if reset and reset.isdigit():
                    delay = max(5.0, int(reset) - time.time() + 2)
                print(f"    rate limited, waiting {delay:.0f}s")
                time.sleep(delay)
                continue
            if response.status_code >= 500 and attempt < MAX_RETRIES:
                delay = 2 ** attempt
                print(f"    HTTP {response.status_code}, retrying in {delay}s")
                time.sleep(delay)
                continue
            raise RuntimeError(f"HTTP {response.status_code} for {path}: {json.dumps(payload)[:300]}")
        raise RuntimeError(f"Gave up after {MAX_RETRIES} attempts for {path}")


def count_month(client: Client, query: str, start: datetime, end: datetime, context: Mapping[str, object]) -> int:
    """Return how many posts match the query in one month."""
    total = 0
    params = {
        "query": query,
        "start_time": api_timestamp(start),
        "end_time": api_timestamp(end),
        "granularity": "day",
    }
    while True:
        payload = client.get("/tweets/counts/all", params, {**context, "phase": "counts"})
        meta = payload.get("meta", {}) or {}
        total += int(meta.get("total_tweet_count", 0))
        token = meta.get("next_token")
        if not token:
            return total
        params = {**params, "next_token": token}


def collect_month(
    client: Client,
    query: str,
    start: datetime,
    end: datetime,
    settings: Mapping[str, object],
    out_file: Path,
    context: Mapping[str, object],
) -> Tuple[int, int, int]:
    """Download one month and store the raw pages. Returns posts, referenced, pages."""
    params = {
        "query": query,
        "start_time": api_timestamp(start),
        "end_time": api_timestamp(end),
        "max_results": str(settings.get("max_results", 500)),
        "tweet.fields": ",".join(settings.get("tweet_fields", [])),
    }
    if settings.get("expand_referenced_posts"):
        params["expansions"] = "referenced_tweets.id,author_id"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    posts = referenced = pages = 0
    with out_file.open("w", encoding="utf-8") as handle:
        while True:
            payload = client.get("/tweets/search/all", params, {**context, "phase": "search"})
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            pages += 1
            posts += len(payload.get("data", []) or [])
            referenced += len(((payload.get("includes", {}) or {}).get("tweets", []) or []))
            if client.over_budget():
                raise BudgetExceeded(
                    f"spent about ${client.cost():.2f}, above the --max-cost limit"
                )
            token = (payload.get("meta", {}) or {}).get("next_token")
            if not token:
                return posts, referenced, pages
            params = {**params, "next_token": token}


def parse_day(value: str) -> datetime:
    """Parse a YYYY-MM-DD or ISO timestamp as an aware UTC datetime."""
    text = value.replace("Z", "+00:00")
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accounts", type=Path, default=ROOT / "config" / "x_accounts.json")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "x_collection.json")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--raw-dir", type=Path, default=None, help="Overrides raw_dir in the configuration.")
    parser.add_argument("--actor", action="append", help="Only this actor_id; repeatable.")
    parser.add_argument("--start", help="Overrides collection_start (YYYY-MM-DD).")
    parser.add_argument("--end", help="Overrides collection_end (YYYY-MM-DD).")
    parser.add_argument("--counts-only", action="store_true", help="Only count and estimate the cost.")
    parser.add_argument(
        "--skip-counts",
        action="store_true",
        help="Download without counting first, saving $0.010 per month already counted.",
    )
    parser.add_argument("--yes", action="store_true", help="Do not ask for confirmation before downloading.")
    parser.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Stop before the estimate, or the running cost, exceeds this many dollars.",
    )
    parser.add_argument("--force", action="store_true", help="Collect months already marked complete again.")
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.counts_only and args.skip_counts:
        print("--counts-only and --skip-counts cannot be used together.")
        return 2
    settings = json.loads(args.config.read_text(encoding="utf-8"))
    accounts = load_accounts(args.accounts)
    if args.actor:
        accounts = [a for a in accounts if a["actor_id"] in set(args.actor)]
        if not accounts:
            print("No account matches --actor.")
            return 2
    missing = [a["actor_id"] for a in accounts if not a.get("x_user_id")]
    if missing:
        print(f"Missing x_user_id for {', '.join(missing)}. Run: python src/check_x_access.py --write-ids")
        return 2

    env = load_env_file(args.env_file)
    token = env.get("X_BEARER_TOKEN", "")
    if not token:
        print(f"X_BEARER_TOKEN not found in {args.env_file}.")
        return 2

    start = parse_day(args.start or str(settings["collection_start"]))
    end_value = args.end or settings.get("collection_end")
    end = parse_day(end_value) if end_value else datetime.now(timezone.utc) - timedelta(minutes=1)
    if start >= end:
        print("The start of the period must be before its end.")
        return 2

    raw_dir = args.raw_dir or (ROOT / str(settings.get("raw_dir", "data/raw/x")))
    manifest_path = raw_dir / "manifest.jsonl"
    completed = {} if args.force else load_manifest(manifest_path)
    client = Client(token, manifest_path, timeout=args.timeout, max_cost=args.max_cost)

    pending: List[Tuple[Dict[str, object], str, datetime, datetime]] = []
    skipped = 0
    for account in accounts:
        for label, slice_start, slice_end in month_slices(start, end):
            if (account["actor_id"], label) in completed:
                skipped += 1
                continue
            pending.append((account, label, slice_start, slice_end))
    print(f"{len(pending)} month(s) to collect, {skipped} already complete.")
    if not pending:
        return 0

    counts: Dict[Tuple[str, str], int] = {}
    if args.skip_counts:
        print("\nSkipping the counting phase: the cost is not known in advance.")
    else:
        print("\nCounting posts (full-archive counts, $0.010 per request)...")
        try:
            for account, label, slice_start, slice_end in pending:
                query = build_query(str(settings["query_template"]), str(account["x_user_id"]))
                context = {"actor_id": account["actor_id"], "month": label, "query": query}
                total = count_month(client, query, slice_start, slice_end, context)
                counts[(str(account["actor_id"]), label)] = total
                print(f"  {account['actor_id']:>22} {label}: {total:>5} posts")
        except AccessError as error:
            print(f"\nAccess error: {error}")
            return 1

        total_posts = sum(counts.values())
        by_actor: Dict[str, int] = {}
        for (actor_id, _), value in counts.items():
            by_actor[actor_id] = by_actor.get(actor_id, 0) + value
        posts_cost = total_posts * PRICE_PER_POST
        print("\nPosts by actor:")
        for actor_id, value in sorted(by_actor.items()):
            print(f"  {actor_id:>22}: {value:>6}")
        print(f"\nTotal posts: {total_posts}")
        print(f"Counts requests so far: {client.counts_requests} (${client.cost():.2f})")
        print(f"Estimated download cost: ${posts_cost:.2f} (referenced posts are extra)")

        if args.counts_only:
            return 0
        if args.max_cost is not None and client.cost() + posts_cost > args.max_cost:
            print(f"Estimate above --max-cost (${args.max_cost:.2f}). Nothing was downloaded.")
            return 1

    if not args.yes:
        question = (
            "\nDownload these posts? [y/N] "
            if counts
            else "\nDownload without a cost estimate? [y/N] "
        )
        answer = input(question).strip().lower()
        if answer not in {"y", "yes"}:
            print("Cancelled. Nothing was downloaded.")
            return 1

    print("\nDownloading...")
    collected_posts = collected_referenced = 0
    try:
        for account, label, slice_start, slice_end in pending:
            actor_id = str(account["actor_id"])
            query = build_query(str(settings["query_template"]), str(account["x_user_id"]))
            context = {"actor_id": actor_id, "month": label, "query": query}
            out_file = raw_dir / actor_id / f"{label}.jsonl"
            posts, referenced, pages = collect_month(
                client, query, slice_start, slice_end, settings, out_file, context
            )
            expected = counts.get((actor_id, label))
            if expected is None:
                status = "not counted"
            else:
                status = "ok" if posts == expected else f"expected {expected}"
            print(f"  {actor_id:>22} {label}: {posts:>5} posts, {referenced:>4} referenced, {pages} page(s) ({status})")
            collected_posts += posts
            collected_referenced += referenced
            append_manifest(
                manifest_path,
                {
                    "type": "month_complete",
                    "actor_id": actor_id,
                    "month": label,
                    "query": query,
                    "start_time": api_timestamp(slice_start),
                    "end_time": api_timestamp(slice_end),
                    "posts": posts,
                    "referenced_posts": referenced,
                    "expected_posts": expected,
                    "pages": pages,
                    "file": str(out_file.relative_to(raw_dir)),
                    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                },
            )
    except (AccessError, BudgetExceeded) as error:
        label = "Access error" if isinstance(error, AccessError) else "Cost limit reached"
        print(f"\n{label}: {error}")
        print("Months already finished are marked complete and will be skipped on the next run.")
        print(f"Approximate cost so far: ${client.cost():.2f}")
        return 1

    print(f"\nCollected {collected_posts} posts and {collected_referenced} referenced posts.")
    print(f"Approximate cost: ${client.cost():.2f} (before same-day deduplication).")
    print(f"Raw pages in {raw_dir}, manifest in {manifest_path}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
