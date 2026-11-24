#!/usr/bin/env python3
"""Absolute response levels: how much the personas supported the posts.

RQ1 to RQ3 report the speaker-attribution effect, the response under a name
minus the response to the anonymous version of the same post. This script
describes the levels behind those differences: the mean stance (-1 reject,
0 neutral, +1 support) and the share of each answer in every condition, over
the same complete persona-post quartets.

Splits, each within a framework and topic scope:
  all            every quartet in the scope
  profile_level  the topic-conditioned high, mid and low profiles (within one
                 framework and topic only, since a level means a different
                 value profile on each topic and in each framework)
  true_author    the leader who actually wrote the post
  group_author   the persona's elicited left-right group x the true author

Levels are descriptive. They depend on how the personas were built and on the
model, so the attribution effect stays the estimand. Confidence intervals are
two-way cluster-robust by message and persona, as in RQ1 and RQ2.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np
import pandas as pd

from analyze_ideological_congruence import (
    ANONYMOUS, DEFAULT_IDEOLOGY_DIR, DEFAULT_REACTIONS_DIR, DEFAULT_STIMULI, KEYS, LEADERS, OUTCOMES, ROOT,
    cell_covariances, cluster_codes, combine, load_kept_messages, load_placements, load_reactions, safe_slug, unit,
)
from elicit_persona_ideology import GROUPS

DEFAULT_POSTS = ROOT / "data" / "processed" / "x_posts_internal.csv"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "absolute_levels"
CONDITIONS = [ANONYMOUS] + LEADERS
LEVELS = ["high", "mid", "low"]


def complete_responses(reactions: pd.DataFrame, kept: set) -> pd.DataFrame:
    """Responses of the persona-post pairs observed in all four conditions, with every outcome."""
    reactions = reactions[reactions["message_id"].isin(kept)]
    wide = reactions.pivot(index=KEYS, columns="speaker_condition", values=OUTCOMES).dropna(how="any")
    mask = reactions.set_index(KEYS).index.isin(wide.index)
    return reactions[mask].reset_index(drop=True)


def profile_levels(reactions_dir: Path, model_slug: str) -> pd.DataFrame:
    files = sorted(reactions_dir.glob(f"*/{model_slug}/*/*/*/responses_active.csv"))
    levels = pd.concat(
        [pd.read_csv(f, usecols=KEYS + ["profile_level"], dtype={"persona_id": str, "message_id": str}) for f in files],
        ignore_index=True,
    ).drop_duplicates(KEYS)
    return levels


def persona_groups(responses: pd.DataFrame, placements: pd.DataFrame) -> pd.DataFrame:
    """Group of each persona, kept only when its placement was given to the same persona text."""
    personas = responses[["framework", "persona_id", "persona_sha256"]].drop_duplicates()
    personas = personas.merge(placements, on=["framework", "persona_id"], how="left")
    personas["valid"] = personas["persona_sha256"] == personas["ideology_sha256"]
    valid = personas.groupby(["framework", "persona_id"])["valid"].all()
    keep = valid[valid].index
    return placements.set_index(["framework", "persona_id"]).loc[keep, ["group"]].reset_index()


def describe(sub: pd.DataFrame, split: pd.Series, scope: dict, split_name: str) -> tuple:
    """Mean stance and answer shares per (split level, condition), and the attribution
    effect of each leader within each split level, with cluster-robust CIs."""
    levels = [l for l in (LEVELS + list(GROUPS)) if l in set(split)] or sorted(set(split))
    if not set(split) <= set(levels):
        levels = sorted(set(split))
    labels = [(l, c) for l in levels for c in CONDITIONS]
    index = {lab: i for i, lab in enumerate(labels)}
    cells = np.array([index[(l, c)] for l, c in zip(split, sub["speaker_condition"])])
    codes = cluster_codes(sub)
    means, sizes, covs = cell_covariances(sub["stance"].to_numpy(dtype=float), cells, len(labels), codes)
    persona = sub["framework"] + "|" + sub["persona_id"]
    frame = pd.DataFrame({"_cell": cells, "stance": sub["stance"].to_numpy(), "persona": persona.to_numpy(),
                          "message_id": sub["message_id"].to_numpy()})
    agg = frame.groupby("_cell").agg(
        support=("stance", lambda s: float((s == 1).mean())),
        neutral=("stance", lambda s: float((s == 0).mean())),
        reject=("stance", lambda s: float((s == -1).mean())),
        n_personas=("persona", "nunique"),
        n_messages=("message_id", "nunique"),
    )
    level_rows, effect_rows = [], []
    for (level, condition), i in index.items():
        level_rows.append({**scope, "split": split_name, "level": level, "condition": condition,
                           **combine(unit(len(labels), i), means, covs), **agg.loc[i].to_dict(),
                           "n_responses": int(sizes[i])})
        if condition != ANONYMOUS:
            w = unit(len(labels), i) - unit(len(labels), index[(level, ANONYMOUS)])
            effect_rows.append({**scope, "split": split_name, "level": level, "leader": condition,
                                **combine(w, means, covs), "n_pairs": int(sizes[i])})
    return level_rows, effect_rows


def analyse(responses: pd.DataFrame) -> tuple:
    level_rows: List[dict] = []
    effect_rows: List[dict] = []
    frameworks = sorted(responses["framework"].unique())
    topics = sorted(responses["topic"].unique())
    for framework in ["all"] + frameworks:
        for topic in ["all"] + topics:
            mask = np.ones(len(responses), dtype=bool)
            if framework != "all":
                mask &= responses["framework"].to_numpy() == framework
            if topic != "all":
                mask &= responses["topic"].to_numpy() == topic
            sub = responses[mask].reset_index(drop=True)
            scope = {"framework": framework, "topic": topic}
            splits = {"all": pd.Series("all", index=sub.index), "true_author": sub["true_author"]}
            if framework != "all" and topic != "all":
                splits["profile_level"] = sub["profile_level"]
            if topic == "all":
                grouped = sub["group"].notna()
                g = sub[grouped].reset_index(drop=True)
                lr, er = describe(g, g["group"] + "|" + g["true_author"], scope, "group_author")
                level_rows += lr
                effect_rows += er
            for name, split in splits.items():
                lr, er = describe(sub, split, scope, name)
                level_rows += lr
                effect_rows += er
    return pd.DataFrame(level_rows), pd.DataFrame(effect_rows)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    parser.add_argument("--frame", default="spain", help="Ideology elicitation used for the groups.")
    parser.add_argument("--reactions-dir", type=Path, default=DEFAULT_REACTIONS_DIR)
    parser.add_argument("--ideology-dir", type=Path, default=DEFAULT_IDEOLOGY_DIR)
    parser.add_argument("--stimuli", type=Path, default=DEFAULT_STIMULI)
    parser.add_argument("--posts", type=Path, default=DEFAULT_POSTS,
                        help="Internal post table with the true author (actor_id) of each message.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    slug = safe_slug(args.model)
    reactions = load_reactions(args.reactions_dir, slug)
    responses = complete_responses(reactions, load_kept_messages(args.stimuli))
    responses = responses.merge(profile_levels(args.reactions_dir, slug), on=KEYS, how="left")
    authors = pd.read_csv(args.posts, usecols=["message_id", "actor_id"], dtype=str).drop_duplicates("message_id")
    responses = responses.merge(authors.rename(columns={"actor_id": "true_author"}), on="message_id", how="left")
    if responses["true_author"].isna().any() or responses["profile_level"].isna().any():
        raise ValueError("Some responses have no true author or no profile level")
    placements = load_placements(args.ideology_dir, slug, args.frame, sorted(responses["framework"].unique()))
    responses = responses.merge(persona_groups(responses, placements), on=["framework", "persona_id"], how="left")

    levels, effects = analyse(responses)
    out_dir = args.output_dir / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    levels.to_csv(out_dir / "levels.csv", index=False)
    effects.to_csv(out_dir / "effects_by_split.csv", index=False)
    pairs = len(responses) // len(CONDITIONS)
    summary = {"model": args.model, "frame": args.frame, "complete_pairs": pairs,
               "responses": len(responses), "posts": int(responses["message_id"].nunique()),
               "personas": int((responses["framework"] + "|" + responses["persona_id"]).nunique())}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    overall = levels[(levels.framework == "all") & (levels.topic == "all") & (levels.split == "all")]
    print(f"{pairs:,} complete persona-post pairs. Stance by condition, both frameworks, all topics:")
    for r in overall.itertuples():
        print(f"  {r.condition:<22} {r.estimate:+.3f} [{r.ci_low:+.3f}, {r.ci_high:+.3f}]  "
              f"support {r.support:.1%}  neutral {r.neutral:.1%}  reject {r.reject:.1%}")
    print(f"Wrote {out_dir}/levels.csv and effects_by_split.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
