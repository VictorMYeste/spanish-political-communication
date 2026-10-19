#!/usr/bin/env python3
"""Rank the RQ3 candidate topics by their salience in the CIS barometers.

Applies the rule in config/cis_topic_selection.json to the table written by
collect_cis_problems.py: drops the excluded CIS categories, merges renamed
labels and the categories mapped to the same topic, averages each topic over
all barometers in the window and marks the top k. The coverage check on the
X posts is a separate step, once the keyword registry exists.

Writes the ranking and an audit table that shows where every CIS category
went.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "cis_topic_selection.json"
DEFAULT_IN = ROOT / "data" / "processed" / "cis_problems_monthly.csv"
DEFAULT_RANKING = ROOT / "data" / "processed" / "cis_topic_ranking.csv"
DEFAULT_AUDIT = ROOT / "data" / "processed" / "cis_category_assignment.csv"

RANKING_FIELDS = [
    "rank",
    "topic",
    "kind",
    "selected",
    "mean_pct",
    "peak_pct",
    "peak_month",
    "barometers_present",
    "categories",
]
AUDIT_FIELDS = [
    "category",
    "assignment",
    "target",
    "barometers_present",
    "mean_pct_when_present",
]


def normalize(label: str) -> str:
    text = unicodedata.normalize("NFKD", label.lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.replace('"', "").split())


class Classifier:
    """Assign a CIS category label to an exclusion, a topic or itself."""

    def __init__(self, config: Dict):
        self.aliases = {normalize(k): v for k, v in config.get("label_aliases", {}).items()}
        self.exclusions = [(re.compile(item["pattern"]), item["group"]) for item in config["exclusions"]]
        self.mapping = [
            (topic, [re.compile(pattern) for pattern in patterns])
            for topic, patterns in config["mapping"].items()
        ]

    def canonical(self, label: str) -> str:
        return self.aliases.get(normalize(label), label)

    def classify(self, label: str) -> Tuple[str, str]:
        label = self.canonical(label)
        key = normalize(label)
        for pattern, group in self.exclusions:
            if pattern.search(key):
                return "excluded", group
        for topic, patterns in self.mapping:
            if any(pattern.search(key) for pattern in patterns):
                return "topic", topic
        return "standalone", label


def read_rows(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def rank_topics(rows: List[Dict[str, str]], config: Dict) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    classifier = Classifier(config)
    barometers = sorted({(int(r["year"]), int(r["month"]), r["study_id"]) for r in rows})
    values: Dict[Tuple[str, str], Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    members: Dict[Tuple[str, str], set] = defaultdict(set)
    audit: Dict[str, Dict[str, object]] = {}

    for row in rows:
        kind, target = classifier.classify(row["category"])
        total = float(row["total_pct"])
        info = audit.setdefault(
            row["category"],
            {"category": row["category"], "assignment": kind, "target": target, "values": []},
        )
        info["values"].append(total)
        if kind == "excluded":
            target = f"[excluded: {target}]"
        values[(kind, target)][row["study_id"]] += total
        members[(kind, target)].add(classifier.canonical(row["category"]))

    ranking: List[Dict[str, object]] = []
    for (kind, target), by_study in values.items():
        series = [(by_study.get(study, 0.0), f"{year}-{month:02d}") for year, month, study in barometers]
        peak_value, peak_month = max(series)
        ranking.append(
            {
                "topic": target,
                "kind": kind,
                "mean_pct": round(sum(v for v, _ in series) / len(series), 2),
                "peak_pct": round(peak_value, 1),
                "peak_month": peak_month,
                "barometers_present": len(by_study),
                "categories": " | ".join(sorted(members[(kind, target)])),
            }
        )
    ranking.sort(key=lambda item: (item["kind"] == "excluded", -item["mean_pct"]))
    position = 0
    for item in ranking:
        if item["kind"] == "excluded":
            item["rank"], item["selected"] = "", False
            continue
        position += 1
        item["rank"] = position
        item["selected"] = position <= config["top_k"]

    audit_rows = [
        {
            "category": info["category"],
            "assignment": info["assignment"],
            "target": info["target"],
            "barometers_present": len(info["values"]),
            "mean_pct_when_present": round(sum(info["values"]) / len(info["values"]), 2),
        }
        for info in sorted(audit.values(), key=lambda item: (item["assignment"], str(item["target"]), item["category"]))
    ]
    return ranking, audit_rows


def write_csv(path: Path, rows: List[Dict[str, object]], fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(ranking: List[Dict[str, object]], limit: int) -> str:
    lines = [
        "| Rank | Topic | Mean % | Peak % (month) | Selected |",
        "| ---: | --- | ---: | --- | --- |",
    ]
    shown = [item for item in ranking if item["kind"] != "excluded"][:limit]
    shown += [item for item in ranking if item["kind"] == "excluded"]
    for item in shown:
        lines.append(
            f"| {item['rank']} | {item['topic']} | {item['mean_pct']:.1f} | "
            f"{item['peak_pct']:.1f} ({item['peak_month']}) | {'yes' if item['selected'] else ''} |"
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--input", type=Path, default=DEFAULT_IN)
    parser.add_argument("--ranking", type=Path, default=DEFAULT_RANKING)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--show", type=int, default=15, help="ranked topics to print")
    args = parser.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    rows = read_rows(args.input)
    ranking, audit_rows = rank_topics(rows, config)
    write_csv(args.ranking, ranking, RANKING_FIELDS)
    write_csv(args.audit, audit_rows, AUDIT_FIELDS)
    studies = len({row["study_id"] for row in rows})
    print(f"{studies} barometers, {len(audit_rows)} CIS categories\n")
    print(markdown_table(ranking, args.show))
    return 0


if __name__ == "__main__":
    sys.exit(main())
