#!/usr/bin/env python3
"""Clean and anonymize the X stimuli and flag implausible attributions.

Steps 3 (quality filters), 6 (anonymization) and 8 (cross-attribution check)
of docs/PROPOSAL.md, section 6, with the rules in config/x_stimulus_filters.json:

* The text is cleaned (HTML entities decoded, URLs removed, whitespace
  collapsed) and anonymized: the leaders become [POLITICAL_ACTOR], their
  parties [PARTY], campaign hashtags [CAMPAIGN_TAG] and other accounts [USER].
  The result goes to text_anon.
* Posts that are too short or have too few words, that still name a leader or
  party, that anonymization leaves too short, or that repeat an earlier text
  get an exclude_reason.
* Role markers show the real author's position: speaking for the Government,
  opposing it, or attacking another leader's camp. implausible_under lists the
  leaders under whose name such a post is implausible.

keep_for_prompt stays False; it is set when the sample is drawn. Run after
build_x_tables.py and assign_x_topics.py. Writes x_stimuli.csv in place and
x_stimuli_qa.csv.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from assign_x_topics import normalize, read_csv, read_header, write_csv

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "x_stimulus_filters.json"
DEFAULT_INTERNAL = ROOT / "data" / "processed" / "x_posts_internal.csv"
DEFAULT_STIMULI = ROOT / "data" / "processed" / "x_stimuli.csv"
DEFAULT_QA = ROOT / "data" / "processed" / "x_stimuli_qa.csv"

URL_RE = re.compile(r"https?://\S+")
WORD_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")
HASHTAG_RE = re.compile(r"#(\w+)")
HANDLE_RE = re.compile(r"@(\w+)")
PLACEHOLDER_RE = re.compile(r"\[(?:POLITICAL_ACTOR|PARTY|CAMPAIGN_TAG|USER)\]")
TOPICS = ["economy_employment", "household_costs", "immigration_border", "corruption"]

QA_FIELDS = [
    "message_id",
    "actor_id",
    "date",
    "topic",
    "exclude_reason",
    "role_groups",
    "role_terms",
    "implausible_under",
]


def clean(text: str) -> str:
    """Decode HTML entities, remove URLs and collapse whitespace."""
    return " ".join(URL_RE.sub(" ", html.unescape(text)).split())


def quality_reasons(text: str, quality: Dict) -> List[str]:
    """Return the quality exclusion reasons of a cleaned text."""
    reasons = []
    if len(text) < quality["min_chars"]:
        reasons.append("too_short")
    if len(WORD_RE.findall(text)) < quality["min_alpha_tokens"]:
        reasons.append("too_few_words")
    return reasons


class Anonymizer:
    """Replaces the leaders, their parties, campaign tags and accounts with placeholders."""

    def __init__(self, spec: Dict):
        self.actor_handles = {handle.lower() for handle in spec["actor_handles"]}
        self.party_handle_re = re.compile(spec["party_handle_pattern"])
        self.party_handles = set(spec["party_handles"])
        self.institutions = spec["institution_handles"]
        self.campaign_re = re.compile(spec["campaign_hashtag_pattern"])
        self.campaign_tags = {tag for tags in spec["campaign_hashtags"].values() for tag in tags}
        self.actors = [re.compile(r"\b" + p + r"\b", re.IGNORECASE) for p in spec["actor_patterns"]]
        self.parties = [re.compile(r"\b" + p + r"\b", re.IGNORECASE) for p in spec["party_patterns"]]
        self.leak_re = re.compile(spec["leak_pattern"])
        self.degraded = spec["degraded"]

    def _hashtag(self, match: re.Match) -> str:
        tag = normalize(match.group(1))
        if tag in self.campaign_tags or self.campaign_re.search(tag):
            return "[CAMPAIGN_TAG]"
        return match.group(0)

    def _handle(self, match: re.Match) -> str:
        handle = match.group(1).lower()
        if handle in self.actor_handles:
            return "[POLITICAL_ACTOR]"
        if handle in self.party_handles or self.party_handle_re.search(handle):
            return "[PARTY]"
        return self.institutions.get(handle, "[USER]")

    def anonymize(self, text: str) -> str:
        text = HASHTAG_RE.sub(self._hashtag, text)
        text = HANDLE_RE.sub(self._handle, text)
        for pattern in self.actors:
            text = pattern.sub("[POLITICAL_ACTOR]", text)
        for pattern in self.parties:
            text = pattern.sub("[PARTY]", text)
        return " ".join(text.split())

    def reasons(self, anon: str) -> List[str]:
        """Return identity_leak and anonymization_degraded where they apply."""
        reasons = []
        if self.leak_re.search(normalize(PLACEHOLDER_RE.sub(" ", anon))):
            reasons.append("identity_leak")
        limits = self.degraded
        words = len(WORD_RE.findall(PLACEHOLDER_RE.sub(" ", anon)))
        placeholders = len(PLACEHOLDER_RE.findall(anon))
        if (
            len(anon) < limits["min_chars"]
            or words < limits["min_words"]
            or (len(anon) <= limits["short_text_chars"] and placeholders > limits["max_placeholders_in_short_text"])
        ):
            reasons.append("anonymization_degraded")
        return reasons


class RoleMarkers:
    """Compiled role-marker groups, each applying to some authors."""

    def __init__(self, spec: Dict):
        self.exclude = [re.compile(r"\b" + phrase + r"\b") for phrase in spec.get("exclude_phrases", [])]
        self.groups = [
            (
                item["group"],
                set(item["authors"]),
                item["implausible_under"],
                re.compile(r"\b(?:" + "|".join(item["patterns"]) + r")\b"),
            )
            for item in spec["groups"]
        ]

    def match(self, text: str, author: str) -> Dict[str, Dict[str, List[str]]]:
        """Return {group: {"terms": [...], "implausible_under": [...]}} for the author's groups that match."""
        normalized = normalize(text)
        for pattern in self.exclude:
            normalized = pattern.sub(" ", normalized)
        hits = {}
        for group, authors, implausible, pattern in self.groups:
            if author not in authors:
                continue
            terms = sorted({m.group(0) for m in pattern.finditer(normalized)})
            if terms:
                hits[group] = {"terms": terms, "implausible_under": implausible}
        return hits


def prepare(
    stimuli: List[Dict[str, str]], internal: List[Dict[str, str]], config: Dict
) -> tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    posts = {row["message_id"]: row for row in internal}
    missing = [row["message_id"] for row in stimuli if row["message_id"] not in posts]
    if missing:
        raise ValueError(f"{len(missing)} stimuli are not in the internal table, for example {missing[0]}")
    markers = RoleMarkers(config["role_markers"])
    anonymizer = Anonymizer(config["anonymization"])
    seen: set = set()
    out, qa = [], []
    for row in stimuli:
        post = posts[row["message_id"]]
        text = clean(post["text"])
        anon = anonymizer.anonymize(text)
        reasons = quality_reasons(text, config["quality"]) + anonymizer.reasons(anon)
        if not reasons:
            # Duplicates are counted among the posts that pass every other check.
            if anon.lower() in seen:
                reasons.append("duplicate_text")
            seen.add(anon.lower())
        hits = markers.match(post["text"], post["actor_id"])
        implausible = sorted({actor for hit in hits.values() for actor in hit["implausible_under"]})
        out.append({**row, "text_anon": anon})
        qa.append(
            {
                "message_id": row["message_id"],
                "actor_id": post["actor_id"],
                "date": row["date"],
                "topic": row["topic"],
                "exclude_reason": "|".join(reasons),
                "role_groups": "|".join(hits),
                "role_terms": "; ".join(term for hit in hits.values() for term in hit["terms"]),
                "implausible_under": "|".join(implausible),
            }
        )
    return out, qa


def summary_tables(qa: List[Dict[str, str]], actors: List[str]) -> str:
    total = Counter(row["actor_id"] for row in qa)
    reasons = Counter((reason, row["actor_id"]) for row in qa for reason in row["exclude_reason"].split("|") if reason)
    kept = [row for row in qa if not row["exclude_reason"]]
    lines = ["| | " + " | ".join(actors) + " |", "| --- |" + " ---: |" * len(actors)]
    lines.append("| Original posts | " + " | ".join(str(total[a]) for a in actors) + " |")
    for reason in ["too_short", "too_few_words", "identity_leak", "anonymization_degraded", "duplicate_text"]:
        lines.append(f"| {reason} | " + " | ".join(str(reasons[(reason, a)]) for a in actors) + " |")
    passed = Counter(row["actor_id"] for row in kept)
    lines.append("| Pass every check | " + " | ".join(str(passed[a]) for a in actors) + " |")

    lines += ["", "Role markers, posts that pass every check (marked / total):", ""]
    lines += ["| Topic | " + " | ".join(actors) + " |", "| --- |" + " ---: |" * len(actors)]
    for topic in TOPICS + ["four topics", "all"]:
        if topic == "four topics":
            rows = [row for row in kept if row["topic"] in TOPICS]
        elif topic == "all":
            rows = kept
        else:
            rows = [row for row in kept if row["topic"] == topic]
        n = Counter(row["actor_id"] for row in rows)
        marked = Counter(row["actor_id"] for row in rows if row["implausible_under"])
        lines.append(f"| {topic} | " + " | ".join(f"{marked[a]} / {n[a]}" for a in actors) + " |")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--internal", type=Path, default=DEFAULT_INTERNAL)
    parser.add_argument("--stimuli", type=Path, default=DEFAULT_STIMULI)
    parser.add_argument("--qa", type=Path, default=DEFAULT_QA)
    args = parser.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    stimuli = read_csv(args.stimuli)
    if any(not row["topic"] for row in stimuli):
        print(f"{args.stimuli} has posts without a topic. Run src/assign_x_topics.py first.")
        return 2
    out, qa = prepare(stimuli, read_csv(args.internal), config)

    write_csv(args.stimuli, out, read_header(args.stimuli))
    write_csv(args.qa, qa, QA_FIELDS)

    actors = sorted({row["actor_id"] for row in qa})
    print(summary_tables(qa, actors))
    print(f"\nWrote {args.stimuli} (cleaned and anonymized text_anon) and {args.qa}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
