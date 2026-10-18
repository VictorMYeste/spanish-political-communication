#!/usr/bin/env python3
"""Assign the RQ3 topics to the X posts and check their coverage.

Matches every post against the keyword registry in config/x_topic_keywords.json.
A post gets a topic when exactly one topic matches, review_needed when several
match, and other when none matches. The coverage check counts original posts
per topic and leader and compares them with the minimum fixed in
config/cis_topic_selection.json (step 5 of docs/TOPIC_SELECTION.md).

Also writes a random sample of matched original posts per topic for a manual
spot check.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "config" / "x_topic_keywords.json"
DEFAULT_SELECTION = ROOT / "config" / "cis_topic_selection.json"
DEFAULT_IN = ROOT / "data" / "processed" / "x_posts_internal.csv"
DEFAULT_OUT = ROOT / "data" / "processed" / "x_topics.csv"
DEFAULT_COVERAGE = ROOT / "data" / "processed" / "x_topic_coverage.csv"
DEFAULT_SPOTCHECK = ROOT / "data" / "processed" / "x_topic_spotcheck.csv"
DEFAULT_SEED = 20261018

URL_RE = re.compile(r"https?://\S+")

OUT_FIELDS = [
    "message_id",
    "post_id",
    "actor_id",
    "post_type",
    "date",
    "topic",
    "topic_candidates",
    "topic_groups",
    "matched_terms",
]
SPOTCHECK_FIELDS = [
    "topic",
    "message_id",
    "actor_id",
    "date",
    "matched_terms",
    "text",
    "is_correct",
    "is_main_topic",
    "note",
]


def normalize(text: str) -> str:
    """Remove URLs, lower-case, strip accents and collapse whitespace."""
    text = URL_RE.sub(" ", text)
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split())


class Registry:
    """Compiled keyword groups per topic."""

    def __init__(self, spec: Dict):
        # Words whose meaning depends on the accent ("paró" stopped, "paro"
        # unemployment) are removed before normalize() strips the accents.
        self.exclude_accented = [
            re.compile(r"\b" + word + r"\b") for word in spec.get("exclude_accented", [])
        ]
        self.exclude = [re.compile(r"\b" + phrase + r"\b") for phrase in spec.get("exclude_phrases", [])]
        self.topics: List[Tuple[str, List[Tuple[str, re.Pattern]]]] = []
        for item in spec["topics"]:
            groups = [
                (group, re.compile(r"\b(?:" + "|".join(patterns) + r")\b"))
                for group, patterns in item["groups"].items()
            ]
            self.topics.append((item["topic"], groups))

    @property
    def names(self) -> List[str]:
        return [topic for topic, _ in self.topics]

    def match(self, text: str) -> Dict[str, Dict[str, List[str]]]:
        """Return {topic: {group: matched strings}} for the topics that match."""
        lowered = text.lower()
        for pattern in self.exclude_accented:
            lowered = pattern.sub(" ", lowered)
        normalized = normalize(lowered)
        for pattern in self.exclude:
            normalized = pattern.sub(" ", normalized)
        hits: Dict[str, Dict[str, List[str]]] = {}
        for topic, groups in self.topics:
            for group, pattern in groups:
                found = [m.group(0) for m in pattern.finditer(normalized)]
                if found:
                    hits.setdefault(topic, {})[group] = found
        return hits


def assign(hits: Dict[str, Dict[str, List[str]]]) -> str:
    if len(hits) == 1:
        return next(iter(hits))
    return "review_needed" if hits else "other"


def label_posts(posts: List[Dict[str, str]], registry: Registry) -> List[Dict[str, str]]:
    labelled = []
    for post in posts:
        hits = registry.match(post["text"])
        labelled.append(
            {
                "message_id": post["message_id"],
                "post_id": post["post_id"],
                "actor_id": post["actor_id"],
                "post_type": post["post_type"],
                "date": post["date"],
                "topic": assign(hits),
                "topic_candidates": "|".join(t for t in registry.names if t in hits),
                "topic_groups": "|".join(f"{t}:{g}" for t in registry.names if t in hits for g in hits[t]),
                "matched_terms": "; ".join(
                    sorted({term for groups in hits.values() for terms in groups.values() for term in terms})
                ),
            }
        )
    return labelled


def coverage(
    labelled: List[Dict[str, str]], topics: List[str], actors: List[str], minimum: int
) -> List[Dict[str, object]]:
    counts = Counter(
        (row["topic"], row["actor_id"]) for row in labelled if row["post_type"] == "original"
    )
    rows = []
    for topic in topics + ["review_needed", "other"]:
        row: Dict[str, object] = {"topic": topic}
        for actor in actors:
            row[actor] = counts[(topic, actor)]
        row["total"] = sum(counts[(topic, actor)] for actor in actors)
        if topic in topics:
            row["min_per_leader"] = min(counts[(topic, actor)] for actor in actors)
            row["kept"] = row["min_per_leader"] >= minimum
        rows.append(row)
    return rows


def spotcheck_sample(
    labelled: List[Dict[str, str]], posts: List[Dict[str, str]], topics: List[str], size: int, seed: int
) -> List[Dict[str, str]]:
    texts = {post["message_id"]: post["text"] for post in posts}
    by_topic: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in labelled:
        if row["post_type"] == "original" and row["topic"] in topics:
            by_topic[row["topic"]].append(row)
    rng = random.Random(seed)
    sample = []
    for topic in topics:
        rows = by_topic[topic]
        for row in sorted(rng.sample(rows, min(size, len(rows))), key=lambda r: r["date"]):
            sample.append(
                {
                    "topic": topic,
                    "message_id": row["message_id"],
                    "actor_id": row["actor_id"],
                    "date": row["date"],
                    "matched_terms": row["matched_terms"],
                    "text": texts[row["message_id"]],
                    "is_correct": "",
                    "is_main_topic": "",
                    "note": "",
                }
            )
    return sample


def write_spotcheck(path: Path, rows: List[Dict[str, str]], overwrite: bool) -> bool:
    """Write the spot-check sample unless an existing, possibly annotated, file would be lost."""
    if path.exists() and not overwrite:
        return False
    write_csv(path, rows, SPOTCHECK_FIELDS)
    return True


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: List[Dict[str, object]], fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(rows: List[Dict[str, object]], actors: List[str], minimum: int) -> str:
    header = "| Topic | " + " | ".join(actors) + " | Total | Kept (>= %d each) |" % minimum
    lines = [header, "| --- |" + " ---: |" * (len(actors) + 1) + " --- |"]
    for row in rows:
        kept = "" if "kept" not in row else ("yes" if row["kept"] else "no")
        lines.append(
            f"| {row['topic']} | " + " | ".join(str(row[a]) for a in actors) + f" | {row['total']} | {kept} |"
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--input", type=Path, default=DEFAULT_IN)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--spotcheck", type=Path, default=DEFAULT_SPOTCHECK)
    parser.add_argument("--spotcheck-size", type=int, default=20, help="posts per topic")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--overwrite-spotcheck",
        action="store_true",
        help="replace an existing spot-check file, which may hold manual annotations",
    )
    args = parser.parse_args(argv)

    registry = Registry(json.loads(args.registry.read_text(encoding="utf-8")))
    minimum = json.loads(args.selection.read_text(encoding="utf-8"))["min_original_posts_per_leader"]
    posts = read_csv(args.input)
    actors = sorted({post["actor_id"] for post in posts})

    labelled = label_posts(posts, registry)
    coverage_rows = coverage(labelled, registry.names, actors, minimum)
    sample = spotcheck_sample(labelled, posts, registry.names, args.spotcheck_size, args.seed)

    write_csv(args.out, labelled, OUT_FIELDS)
    write_csv(args.coverage, coverage_rows, ["topic"] + actors + ["total", "min_per_leader", "kept"])
    wrote_sample = write_spotcheck(args.spotcheck, sample, args.overwrite_spotcheck)

    originals = sum(1 for post in posts if post["post_type"] == "original")
    print(f"{len(posts)} posts, {originals} original. Coverage on original posts:\n")
    print(markdown_table(coverage_rows, actors, minimum))
    print(f"\nWrote {args.out} and {args.coverage}")
    if wrote_sample:
        print(f"Wrote {len(sample)} spot-check rows to {args.spotcheck}")
    else:
        print(f"Kept the existing {args.spotcheck}; use --overwrite-spotcheck to draw a new sample")
    return 0


if __name__ == "__main__":
    sys.exit(main())
