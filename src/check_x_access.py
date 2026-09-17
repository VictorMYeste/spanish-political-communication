#!/usr/bin/env python3
"""Check whether the X app credentials can read public data with the v2 API.

By default the script makes a single user-lookup request for the accounts in
config/x_accounts.json (one user read per account, $0.010 each on
pay-per-use). If the request is rejected, for example because the account has
no credits, nothing is charged. With --probe-archive it also makes one
full-archive counts request ($0.010) to confirm full-archive access.

Credentials are read from the environment or from a local .env file and are
never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

import requests

API_BASE = "https://api.x.com/2"
ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "spanish-political-communication/0.1 (academic research)"
# Response headers worth reporting: rate limits and any billing information.
HEADER_HINTS = ("rate-limit", "credit", "usage", "cost", "billing")

# Short guidance printed for each access status.
EXPLANATIONS = {
    "ok": "The credentials can read public data with the v2 API.",
    "invalid-credentials": (
        "The bearer token was rejected. Regenerate it in the developer console "
        "and update .env."
    ),
    "not-enrolled": (
        "The app is not enrolled for v2 reads. Check that it is attached to a "
        "Project and that the account is enrolled in pay-per-use."
    ),
    "no-credits": (
        "The app is enrolled but cannot pay for reads. Add credits or raise the "
        "spending limit in the developer console."
    ),
    "usage-capped": "A usage cap or spending limit has been reached.",
    "rate-limited": "Rate limit reached. Wait for the reset time and retry.",
    "forbidden": "The request was forbidden. Read the error detail below.",
    "unexpected": "Unexpected response. Read the error detail below.",
}


def load_env_file(path: Path) -> Dict[str, str]:
    """Parse simple KEY=VALUE lines from a .env file."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_accounts(path: Path) -> List[Dict[str, object]]:
    """Load and validate the X accounts configuration."""
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list) or not payload:
        raise ValueError("Expected a non-empty JSON list.")
    for account in payload:
        if not account.get("actor_id") or not account.get("x_handle"):
            raise ValueError(f"Account without actor_id or x_handle: {account}")
    return payload


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace any credential value that appears in text."""
    for secret in secrets:
        if secret and len(secret) >= 8:
            text = text.replace(secret, "<redacted>")
    return text


def classify_response(status: int, payload: Mapping[str, object]) -> str:
    """Map an HTTP status and X error payload to an access status."""
    blob = json.dumps(payload).lower()
    if status == 200:
        return "ok"
    if status == 401:
        return "invalid-credentials"
    if status == 403 and "client-not-enrolled" in blob:
        return "not-enrolled"
    if status == 402 or "credit" in blob:
        return "no-credits"
    if status == 403:
        return "forbidden"
    if status == 429 and ("usagecap" in blob or "usage-capped" in blob):
        return "usage-capped"
    if status == 429:
        return "rate-limited"
    return "unexpected"


def get(
    path: str, token: str, params: Mapping[str, str], timeout: float
) -> Tuple[int, Dict[str, object], Mapping[str, str]]:
    """Send one authenticated GET request to the X API."""
    response = requests.get(
        f"{API_BASE}{path}",
        params=params,
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
        timeout=timeout,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_body": response.text[:500]}
    return response.status_code, payload, response.headers


def report(
    label: str,
    status: int,
    payload: Mapping[str, object],
    headers: Mapping[str, str],
    secrets: Iterable[str],
) -> str:
    """Print the status, relevant headers and error details of a response."""
    access = classify_response(status, payload)
    print(f"\n[{label}] HTTP {status} -> {access}")
    print(f"  {EXPLANATIONS[access]}")
    for name, value in headers.items():
        if any(hint in name.lower() for hint in HEADER_HINTS):
            print(f"  header {name}: {value}")
    errors = list(payload.get("errors") or [])
    if access != "ok":
        errors.insert(0, payload)
    for error in errors:
        if not isinstance(error, dict):
            continue
        fields = {
            key: error[key]
            for key in ("title", "detail", "reason", "type", "value", "message", "code")
            if key in error
        }
        if fields:
            print("  error: " + redact(json.dumps(fields, ensure_ascii=False), secrets))
    return access


def write_user_ids(path: Path, users: List[Dict[str, object]]) -> int:
    """Store resolved user IDs in the accounts configuration."""
    accounts = load_accounts(path)
    by_handle = {str(user["username"]).lower(): str(user["id"]) for user in users}
    updated = 0
    for account in accounts:
        user_id = by_handle.get(str(account["x_handle"]).lower())
        if user_id and account.get("x_user_id") != user_id:
            account["x_user_id"] = user_id
            updated += 1
    path.write_text(json.dumps(accounts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return updated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--accounts", type=Path, default=ROOT / "config" / "x_accounts.json"
    )
    parser.add_argument(
        "--probe-archive",
        action="store_true",
        help="Also make one full-archive counts request ($0.010).",
    )
    parser.add_argument(
        "--write-ids",
        action="store_true",
        help="Store the resolved user IDs in the accounts configuration.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env = {**load_env_file(args.env_file), **os.environ}
    token = env.get("X_BEARER_TOKEN", "")
    if not token:
        print(f"X_BEARER_TOKEN not found in the environment or {args.env_file}.")
        return 2
    secrets = [value for key, value in env.items() if key.startswith("X_")]
    accounts = load_accounts(args.accounts)
    handles = [str(account["x_handle"]) for account in accounts]

    status, payload, headers = get(
        "/users/by",
        token,
        {
            "usernames": ",".join(handles),
            "user.fields": "created_at,public_metrics,verified_type",
        },
        args.timeout,
    )
    access = report("user lookup", status, payload, headers, secrets)
    if access != "ok":
        return 1

    users = [user for user in payload.get("data", []) if isinstance(user, dict)]
    for user in users:
        metrics = user.get("public_metrics", {})
        print(
            f"  @{user.get('username')}: id={user.get('id')} "
            f"created_at={user.get('created_at')} "
            f"posts={metrics.get('tweet_count')} followers={metrics.get('followers_count')}"
        )
    if args.write_ids:
        updated = write_user_ids(args.accounts, users)
        print(f"  updated {updated} user IDs in {args.accounts}")

    if args.probe_archive and users:
        end = datetime.now(timezone.utc) - timedelta(minutes=1)
        start = end - timedelta(days=30)
        status, payload, headers = get(
            "/tweets/counts/all",
            token,
            {
                "query": f"from:{users[0]['id']} -is:retweet",
                "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_time": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "granularity": "day",
            },
            args.timeout,
        )
        access = report("full-archive counts", status, payload, headers, secrets)
        if access != "ok":
            return 1
        total = payload.get("meta", {}).get("total_tweet_count")
        print(f"  @{users[0].get('username')}: {total} posts (no retweets) in the last 30 days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
