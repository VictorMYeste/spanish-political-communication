#!/usr/bin/env python3
"""Build the X internal and stimuli tables from the raw collected pages.

Reads the response pages written by collect_x.py, removes duplicates, and
writes the two tables described in docs/DATA_DICTIONARY.md: a detailed
internal table with provenance, and a stimuli table whose columns are shared
with the Congreso pipeline.

The text of referenced posts (the quoted or replied-to post) is kept in the
internal table so that short posts can be interpreted in context. Whether that
context reaches the prompts is decided later, during preprocessing.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SOURCE_TYPE = "x_post"
LOCAL_TZ = ZoneInfo("Europe/Madrid")

# Columns written to the detailed internal posts CSV file.
INTERNAL_FIELDS = [
    "message_id",
    "bundle_id",
    "actor_id",
    "actor_name",
    "party",
    "date",
    "text",
    "author_role",
    "source_type",
    "post_id",
    "x_user_id",
    "created_at_utc",
    "post_type",
    "referenced_post_id",
    "referenced_post_author_id",
    "referenced_post_text",
    "in_reply_to_user_id",
    "conversation_id",
    "lang",
    "is_long_post",
    "edit_history_post_ids",
    "retweet_count",
    "reply_count",
    "like_count",
    "quote_count",
    "bookmark_count",
    "impression_count",
    "post_url",
    "raw_file",
    "retrieved_at_utc",
]

# Columns written to the pseudonymised stimuli CSV file.
# Must stay identical to STIMULUS_FIELDS in src/extract_congreso_text.py.
STIMULUS_FIELDS = [
    "tweet_id",
    "message_id",
    "bundle_id",
    "text_anon",
    "date",
    "author_role",
    "source_type",
    "topic",
    "keep_for_prompt",
    "is_morally_relevant",
    "dominant_moral_dimension",
    "moral_max",
    "isDeleted",
]


def load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def iter_pages(raw_dir: Path, actor_id: str) -> Iterable[Tuple[Path, Dict]]:
    """Yield every stored response page for one actor, oldest month first."""
    actor_dir = raw_dir / actor_id
    if not actor_dir.is_dir():
        return
    for path in sorted(actor_dir.glob("*.jsonl")):
        # Split on newlines only: post text can contain U+2028, which
        # str.splitlines() would treat as a line break, cutting a page in two.
        for line in path.read_text(encoding="utf-8").split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if isinstance(payload, dict):
                yield path, payload


def post_text(post: Mapping[str, object]) -> Tuple[str, bool]:
    """Return the full text of a post and whether it came from note_tweet."""
    note = post.get("note_tweet")
    if isinstance(note, dict) and note.get("text"):
        return str(note["text"]), True
    return str(post.get("text", "")), False


def classify_post(post: Mapping[str, object]) -> Tuple[str, Optional[str]]:
    """Return the post type and the identifier of the post it refers to."""
    references = post.get("referenced_tweets")
    quoted = replied = None
    if isinstance(references, list):
        for reference in references:
            if not isinstance(reference, dict):
                continue
            if reference.get("type") == "quoted":
                quoted = str(reference.get("id"))
            elif reference.get("type") == "replied_to":
                replied = str(reference.get("id"))
    if quoted:
        return "quote", quoted
    if replied:
        return "reply", replied
    return "original", None


def local_date(created_at: str) -> str:
    """Convert an API timestamp to a local date in Europe/Madrid."""
    if not created_at:
        return ""
    moment = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(LOCAL_TZ).date().isoformat()


def collect_referenced(raw_dir: Path, actor_ids: Iterable[str]) -> Dict[str, Dict[str, str]]:
    """Map referenced post IDs to their text and author, across all pages."""
    referenced: Dict[str, Dict[str, str]] = {}
    for actor_id in actor_ids:
        for _, payload in iter_pages(raw_dir, actor_id):
            includes = payload.get("includes") or {}
            for post in includes.get("tweets") or []:
                if not isinstance(post, dict) or not post.get("id"):
                    continue
                text, _ = post_text(post)
                referenced[str(post["id"])] = {
                    "text": text,
                    "author_id": str(post.get("author_id", "")),
                }
    return referenced


def manifest_timestamps(raw_dir: Path) -> Dict[Tuple[str, str], str]:
    """Map (actor, month) to the time its collection finished."""
    path = raw_dir / "manifest.jsonl"
    stamps: Dict[Tuple[str, str], str] = {}
    if not path.exists():
        return stamps
    for line in path.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("type") == "month_complete":
            stamps[(str(record.get("actor_id")), str(record.get("month")))] = str(
                record.get("finished_at_utc", "")
            )
    return stamps


def build_rows(
    raw_dir: Path,
    accounts: List[Mapping[str, object]],
    politicians: Mapping[str, Mapping[str, object]],
) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    """Build the internal rows and a summary of the build."""
    actor_ids = [str(account["actor_id"]) for account in accounts]
    referenced = collect_referenced(raw_dir, actor_ids)
    stamps = manifest_timestamps(raw_dir)
    rows: List[Dict[str, object]] = []
    seen: set = set()
    duplicates = 0
    missing_context = 0
    types: Counter = Counter()

    for account in accounts:
        actor_id = str(account["actor_id"])
        politician = politicians.get(actor_id, {})
        handle = str(account.get("x_handle", ""))
        for path, payload in iter_pages(raw_dir, actor_id):
            for post in payload.get("data") or []:
                if not isinstance(post, dict) or not post.get("id"):
                    continue
                post_id = str(post["id"])
                if post_id in seen:
                    duplicates += 1
                    continue
                seen.add(post_id)
                text, is_long = post_text(post)
                post_type, reference_id = classify_post(post)
                types[post_type] += 1
                context = referenced.get(reference_id or "", {})
                if reference_id and not context:
                    missing_context += 1
                metrics = post.get("public_metrics") or {}
                created_at = str(post.get("created_at", ""))
                rows.append(
                    {
                        "message_id": f"x_{post_id}",
                        "bundle_id": "",
                        "actor_id": actor_id,
                        "actor_name": politician.get("display_name", ""),
                        "party": politician.get("party", ""),
                        "date": local_date(created_at),
                        "text": text,
                        "author_role": "",
                        "source_type": SOURCE_TYPE,
                        "post_id": post_id,
                        "x_user_id": str(post.get("author_id", account.get("x_user_id", "") or "")),
                        "created_at_utc": created_at,
                        "post_type": post_type,
                        "referenced_post_id": reference_id or "",
                        "referenced_post_author_id": context.get("author_id", ""),
                        "referenced_post_text": context.get("text", ""),
                        "in_reply_to_user_id": str(post.get("in_reply_to_user_id", "") or ""),
                        "conversation_id": str(post.get("conversation_id", "") or ""),
                        "lang": str(post.get("lang", "") or ""),
                        "is_long_post": str(is_long),
                        "edit_history_post_ids": ";".join(
                            str(value) for value in (post.get("edit_history_tweet_ids") or [])
                        ),
                        "retweet_count": metrics.get("retweet_count", ""),
                        "reply_count": metrics.get("reply_count", ""),
                        "like_count": metrics.get("like_count", ""),
                        "quote_count": metrics.get("quote_count", ""),
                        "bookmark_count": metrics.get("bookmark_count", ""),
                        "impression_count": metrics.get("impression_count", ""),
                        "post_url": f"https://x.com/{handle}/status/{post_id}" if handle else "",
                        "raw_file": str(path.relative_to(raw_dir)),
                        "retrieved_at_utc": stamps.get((actor_id, path.stem), ""),
                    }
                )

    rows.sort(key=lambda row: (str(row["date"]), str(row["actor_id"]), str(row["post_id"])))
    dates = [str(row["date"]) for row in rows if row["date"]]
    summary = {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "posts": len(rows),
        "duplicates_skipped": duplicates,
        "posts_by_actor": dict(Counter(str(row["actor_id"]) for row in rows)),
        "posts_by_type": dict(types),
        "referenced_posts_available": len(referenced),
        "references_without_text": missing_context,
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
    }
    return rows, summary


def stimulus_rows(rows: Iterable[Mapping[str, object]]) -> List[Dict[str, object]]:
    """Derive the stimuli table from the internal rows."""
    return [
        {
            "tweet_id": row["post_id"],
            "message_id": row["message_id"],
            "bundle_id": row["bundle_id"],
            "text_anon": row["text"],
            "date": row["date"],
            "author_role": row["author_role"],
            "source_type": row["source_type"],
            "topic": "",
            "keep_for_prompt": "False",
            "is_morally_relevant": "",
            "dominant_moral_dimension": "",
            "moral_max": "",
            "isDeleted": "False",
        }
        for row in rows
    ]


def write_csv(path: Path, fields: List[str], rows: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw" / "x")
    parser.add_argument("--accounts", type=Path, default=ROOT / "config" / "x_accounts.json")
    parser.add_argument("--politicians", type=Path, default=ROOT / "config" / "politicians.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "processed")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.raw_dir.is_dir():
        print(f"No raw data in {args.raw_dir}. Run src/collect_x.py first.")
        return 2
    accounts = load_json(args.accounts)
    politicians = {
        str(item["actor_id"]): item for item in load_json(args.politicians)
    }
    rows, summary = build_rows(args.raw_dir, accounts, politicians)
    if not rows:
        print(f"No posts found in {args.raw_dir}.")
        return 1

    internal_path = args.output_dir / "x_posts_internal.csv"
    stimuli_path = args.output_dir / "x_stimuli.csv"
    summary_path = args.output_dir / "x_build_summary.json"
    write_csv(internal_path, INTERNAL_FIELDS, rows)
    write_csv(stimuli_path, STIMULUS_FIELDS, stimulus_rows(rows))
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"{summary['posts']} posts, {summary['duplicates_skipped']} duplicates skipped")
    for actor_id, value in sorted(summary["posts_by_actor"].items()):
        print(f"  {actor_id:>22}: {value}")
    print(f"  by type: {summary['posts_by_type']}")
    print(f"  references without stored text: {summary['references_without_text']}")
    print(f"  dates: {summary['first_date']} to {summary['last_date']}")
    print(f"Written: {internal_path}, {stimuli_path}, {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
