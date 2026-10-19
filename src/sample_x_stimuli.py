#!/usr/bin/env python3
"""Draw the stimulus sample: the same number of posts per topic and real author.

Takes the posts that pass every check of prepare_x_stimuli.py and have one of
the RQ3 topics, and draws up to --per-cell posts for each topic and real
author, or all of them where a cell has fewer. --other-per-author adds posts
without a topic, and --morally-relevant-only keeps only the posts marked
morally relevant, once that filter has been run.

Sets keep_for_prompt to True for the sampled posts and False for the rest, and
writes a manifest with the parameters, the counts and the sampled message IDs.
--dry-run prints the counts and the number of model calls without writing.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from assign_x_topics import read_csv, read_header, write_csv

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STIMULI = ROOT / "data" / "processed" / "x_stimuli.csv"
DEFAULT_QA = ROOT / "data" / "processed" / "x_stimuli_qa.csv"
DEFAULT_MANIFEST = ROOT / "data" / "processed" / "x_sample_manifest.json"
DEFAULT_SEED = 20261019

NOT_TOPICS = {"other", "review_needed"}
CONDITIONS = 4  # no author, and each of the three leaders
LEADERS = 3

Cell = Tuple[str, str]


def eligible(
    stimuli: List[Dict[str, str]], qa: List[Dict[str, str]], morally_relevant_only: bool
) -> List[Dict[str, str]]:
    """Posts that pass every check, with their real author and topic."""
    checks = {row["message_id"]: row for row in qa}
    missing = [row["message_id"] for row in stimuli if row["message_id"] not in checks]
    if missing:
        raise ValueError(f"{len(missing)} stimuli are not in the QA table, for example {missing[0]}")
    if morally_relevant_only and any(not row["is_morally_relevant"] for row in stimuli):
        raise ValueError("is_morally_relevant is empty; run the moral-relevance filter first")
    rows = []
    for row in stimuli:
        check = checks[row["message_id"]]
        if check["exclude_reason"]:
            continue
        if morally_relevant_only and row["is_morally_relevant"].lower() not in {"true", "1"}:
            continue
        rows.append({"message_id": row["message_id"], "actor_id": check["actor_id"], "topic": row["topic"]})
    return rows


def draw(
    rows: List[Dict[str, str]], per_cell: int, other_per_author: int, seed: int
) -> Tuple[Dict[Cell, List[str]], Counter]:
    """Return the sampled message IDs and the eligible counts per (topic, actor) cell."""
    cells: Dict[Cell, List[str]] = {}
    for row in rows:
        topic = row["topic"]
        if topic == "review_needed" or (topic == "other" and other_per_author == 0):
            continue
        cells.setdefault((topic, row["actor_id"]), []).append(row["message_id"])
    rng = random.Random(seed)
    sample = {}
    for cell in sorted(cells):
        ids = sorted(cells[cell])
        size = other_per_author if cell[0] == "other" else per_cell
        sample[cell] = sorted(rng.sample(ids, min(size, len(ids))))
    return sample, Counter({cell: len(ids) for cell, ids in cells.items()})


def model_calls(posts: int, personas: Optional[int]) -> Dict[str, Optional[int]]:
    return {
        "plausibility_check": posts * LEADERS,
        "persona_judgments": posts * CONDITIONS * personas if personas else None,
    }


def summary(sample: Dict[Cell, List[str]], available: Counter, calls: Dict[str, Optional[int]]) -> str:
    topics = sorted({topic for topic, _ in available}, key=lambda t: (t == "other", t))
    actors = sorted({actor for _, actor in available})
    lines = ["Sampled / eligible:", "", "| Topic | " + " | ".join(actors) + " |", "| --- |" + " ---: |" * len(actors)]
    for topic in topics:
        lines.append(
            f"| {topic} | "
            + " | ".join(f"{len(sample.get((topic, a), []))} / {available[(topic, a)]}" for a in actors)
            + " |"
        )
    totals = Counter()
    for (_, actor), ids in sample.items():
        totals[actor] += len(ids)
    lines.append("| Total | " + " | ".join(str(totals[a]) for a in actors) + " |")
    lines += ["", f"Posts: {sum(totals.values())}", f"Plausibility check: {calls['plausibility_check']} calls (posts x {LEADERS} leaders)"]
    if calls["persona_judgments"] is not None:
        lines.append(f"Persona judgments: {calls['persona_judgments']} calls (posts x {CONDITIONS} conditions x personas)")
    else:
        lines.append(f"Persona judgments: posts x {CONDITIONS} conditions x personas (pass --personas to compute)")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--per-cell", type=int, required=True, help="posts per topic and real author")
    parser.add_argument("--other-per-author", type=int, default=0, help="posts without a topic per author (default 0)")
    parser.add_argument("--morally-relevant-only", action="store_true")
    parser.add_argument("--personas", type=int, help="number of personas, only to count model calls")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--stimuli", type=Path, default=DEFAULT_STIMULI)
    parser.add_argument("--qa", type=Path, default=DEFAULT_QA)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dry-run", action="store_true", help="print the counts without writing")
    args = parser.parse_args(argv)
    if args.per_cell < 1 or args.other_per_author < 0:
        parser.error("--per-cell must be at least 1 and --other-per-author at least 0")

    stimuli = read_csv(args.stimuli)
    rows = eligible(stimuli, read_csv(args.qa), args.morally_relevant_only)
    sample, available = draw(rows, args.per_cell, args.other_per_author, args.seed)
    sampled = {message_id for ids in sample.values() for message_id in ids}
    calls = model_calls(len(sampled), args.personas)
    print(summary(sample, available, calls))
    if args.dry_run:
        return 0

    out = [{**row, "keep_for_prompt": str(row["message_id"] in sampled)} for row in stimuli]
    write_csv(args.stimuli, out, read_header(args.stimuli))
    manifest = {
        "seed": args.seed,
        "per_cell": args.per_cell,
        "other_per_author": args.other_per_author,
        "morally_relevant_only": args.morally_relevant_only,
        "posts": len(sampled),
        "cells": [
            {"topic": topic, "actor_id": actor, "eligible": available[(topic, actor)], "message_ids": ids}
            for (topic, actor), ids in sorted(sample.items())
        ],
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nSet keep_for_prompt in {args.stimuli} and wrote {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
