#!/usr/bin/env python3
"""RQ2: does the speaker-attribution effect depend on the persona's ideology?

As in the RQ1 analysis, the attribution effect of a persona-post pair is the
response in an attributed condition minus the response in the anonymous one,
over the pairs observed in all four conditions. This script averages it by
persona group (left, centre, right, from the elicited CIS self-placement) and
attributed leader, and tests congruence: whether a group penalises the leader
closer to it less than the other group does. Design: docs/RQ2_ANALYSIS.md.

Confidence intervals are two-way cluster-robust by message and persona
(Cameron, Gelbach and Miller 2011), as in RQ1. Posts that failed the stimulus
checks (keep_for_prompt false) are dropped.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

from elicit_persona_ideology import FIELD, GROUPS, QUESTIONS, group_of

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REACTIONS_DIR = ROOT / "data" / "spanish_reactions"
DEFAULT_IDEOLOGY_DIR = ROOT / "data" / "persona_ideology"
DEFAULT_STIMULI = ROOT / "data" / "processed" / "x_stimuli.csv"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "rq2_ideological_congruence"

ANONYMOUS = "anonymous"
# Left to right, as the persona model places them in RQ0 (medians 3, 8 and 9).
LEADERS = ["pedro_sanchez", "alberto_nunez_feijoo", "santiago_abascal"]
LEADER_PAIRS = [(a, b) for i, a in enumerate(LEADERS) for b in LEADERS[i + 1 :]]
STANCE = {"reject": -1, "neutral": 0, "support": 1}
OUTCOMES = ["stance", "agreement", "legitimacy", "offensiveness", "endorsement"]
KEYS = ["framework", "topic", "persona_id", "message_id"]
Z95 = 1.959963984540054


def safe_slug(value: str) -> str:
    """Folder name of a model, as in the reaction and ideology outputs."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._-")


def load_reactions(reactions_dir: Path, model_slug: str) -> pd.DataFrame:
    files = sorted(reactions_dir.glob(f"*/{model_slug}/*/*/*/responses_active.csv"))
    if not files:
        raise FileNotFoundError(f"No responses_active.csv for {model_slug} under {reactions_dir}")
    columns = KEYS + ["speaker_condition", "persona_sha256"] + OUTCOMES
    data = pd.concat(
        [pd.read_csv(f, usecols=columns, dtype={"persona_id": str, "message_id": str}) for f in files],
        ignore_index=True,
    )
    duplicated = data.duplicated(KEYS + ["speaker_condition"])
    if duplicated.any():
        raise ValueError(f"{int(duplicated.sum())} responses appear more than once under {reactions_dir}")
    conditions = set(data["speaker_condition"]) - {ANONYMOUS, *LEADERS}
    if conditions:
        raise ValueError(f"Unknown speaker conditions: {sorted(conditions)}")
    stances = set(data["stance"].dropna()) - set(STANCE)
    if stances:
        raise ValueError(f"Unknown stance values: {sorted(stances)}")
    data["stance"] = data["stance"].map(STANCE)
    return data


def load_kept_messages(stimuli: Path) -> Set[str]:
    table = pd.read_csv(stimuli, usecols=["message_id", "keep_for_prompt"], dtype=str)
    kept = table["keep_for_prompt"].fillna("").str.strip().str.lower().isin(["true", "1"])
    return set(table.loc[kept, "message_id"])


def load_placements(ideology_dir: Path, model_slug: str, frame: str, frameworks: Sequence[str]) -> pd.DataFrame:
    frames = []
    for framework in frameworks:
        path = ideology_dir / model_slug / f"{framework}_{frame}.csv"
        if not path.exists():
            raise FileNotFoundError(f"{path} is missing: run src/elicit_persona_ideology.py --framework {framework}")
        table = pd.read_csv(path, dtype={"persona_id": str})
        table = table[table["status"] == "success"]
        frames.append(table[["framework", "persona_id", "persona_sha256", FIELD]])
    placements = pd.concat(frames, ignore_index=True)
    placements = placements.rename(columns={FIELD: "placement", "persona_sha256": "ideology_sha256"})
    placements["placement"] = placements["placement"].astype(int)
    placements["group"] = placements["placement"].map(group_of)
    return placements


def build_effects(
    reactions: pd.DataFrame, kept: Set[str], placements: pd.DataFrame
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """One row per persona, post and attributed leader: attributed minus anonymous."""
    run_posts = set(reactions["message_id"])
    dropped = sorted(run_posts - kept)
    reactions = reactions[reactions["message_id"].isin(kept)]

    wide = reactions.pivot(index=KEYS, columns="speaker_condition", values=OUTCOMES)
    pairs_expected = len(wide)
    wide = wide.dropna(how="any")
    if wide.empty:
        raise ValueError("No persona-post pair has a response in all four conditions")

    parts = []
    for leader in LEADERS:
        part = pd.DataFrame({o: wide[(o, leader)] - wide[(o, ANONYMOUS)] for o in OUTCOMES}, index=wide.index)
        parts.append(part.reset_index().assign(leader=leader))
    effects = pd.concat(parts, ignore_index=True)

    # A persona is grouped only if its placement was given to the same persona text.
    personas = reactions[["framework", "persona_id", "persona_sha256"]].drop_duplicates()
    personas = personas.merge(placements, on=["framework", "persona_id"], how="left")
    personas["valid"] = personas["persona_sha256"] == personas["ideology_sha256"]
    valid = personas.groupby(["framework", "persona_id"])["valid"].all()
    excluded = sorted(f"{fw}:{pid}" for (fw, pid), ok in valid.items() if not ok)
    grouped = placements.set_index(["framework", "persona_id"]).loc[valid[valid].index, ["placement", "group"]]
    effects = effects.merge(grouped.reset_index(), on=["framework", "persona_id"], how="inner")

    counts: Dict[str, object] = {
        "posts_run": len(run_posts),
        "posts_dropped_keep_for_prompt": dropped,
        "pairs_expected": pairs_expected,
        "pairs_complete": len(wide),
        "personas_excluded_no_matching_placement": excluded,
        "pairs_analysed": int(len(effects) / len(LEADERS)),
        "personas_by_group": {
            fw: {g: int(n) for g, n in grouped.loc[fw, "group"].value_counts().reindex(list(GROUPS), fill_value=0).items()}
            for fw in sorted(grouped.index.get_level_values("framework").unique())
        },
    }
    return effects, counts


def cluster_codes(effects: pd.DataFrame) -> Dict[str, np.ndarray]:
    persona = effects["framework"] + "|" + effects["persona_id"]
    return {
        "message": pd.factorize(effects["message_id"])[0],
        "persona": pd.factorize(persona)[0],
        "pair": pd.factorize(persona + "|" + effects["message_id"])[0],
    }


def cell_covariances(
    values: np.ndarray, cells: np.ndarray, n_cells: int, codes: Dict[str, np.ndarray]
) -> Tuple[np.ndarray, np.ndarray, Dict[str, np.ndarray]]:
    """Cell means, cell sizes and the cluster-robust covariance of the means by
    message, by persona and by persona-post pair (the intersection)."""
    sizes = np.bincount(cells, minlength=n_cells)
    means = np.bincount(cells, weights=values, minlength=n_cells) / sizes
    influence = (values - means[cells]) / sizes[cells]
    covs = {}
    for name, code in codes.items():
        n_clusters_total = int(code.max()) + 1
        sums = np.bincount(code * n_cells + cells, weights=influence, minlength=n_clusters_total * n_cells)
        sums = sums.reshape(n_clusters_total, n_cells)
        g = int(np.count_nonzero(np.bincount(code, minlength=n_clusters_total)))
        covs[name] = (g / (g - 1) if g > 1 else 1.0) * sums.T @ sums
    return means, sizes, covs


def combine(weights: np.ndarray, means: np.ndarray, covs: Dict[str, np.ndarray]) -> Dict[str, float]:
    """Estimate and 95% CI of a linear combination of cell means."""
    var = weights @ (covs["message"] + covs["persona"] - covs["pair"]) @ weights
    if var <= 0:  # two-way variance can be negative in small samples; use the larger one-way one
        var = max(weights @ covs["message"] @ weights, weights @ covs["persona"] @ weights)
    estimate, se = float(weights @ means), float(np.sqrt(max(var, 0.0)))
    return {"estimate": estimate, "se": se, "ci_low": estimate - Z95 * se, "ci_high": estimate + Z95 * se}


def cell_layout(levels_of_rows: pd.Series, leaders: pd.Series) -> Tuple[List[tuple], np.ndarray]:
    """Cells (level, leader) in a fixed order, and the cell of every row."""
    levels = sorted(levels_of_rows.unique(), key=lambda g: list(GROUPS).index(g) if g in GROUPS else g)
    level_index = {g: i for i, g in enumerate(levels)}
    leader_index = {l: i for i, l in enumerate(LEADERS)}
    cells = levels_of_rows.map(level_index).to_numpy() * len(LEADERS) + leaders.map(leader_index).to_numpy()
    return [(g, l) for g in levels for l in LEADERS], cells


def scopes(effects: pd.DataFrame) -> Iterator[Tuple[str, str, np.ndarray]]:
    for framework in ["all"] + sorted(effects["framework"].unique()):
        for topic in ["all"] + sorted(effects["topic"].unique()):
            mask = np.ones(len(effects), dtype=bool)
            if framework != "all":
                mask &= effects["framework"].to_numpy() == framework
            if topic != "all":
                mask &= effects["topic"].to_numpy() == topic
            if mask.any():
                yield framework, topic, mask


def cell_counts(sub: pd.DataFrame, levels_of_rows: pd.Series) -> pd.DataFrame:
    persona = sub["framework"] + "|" + sub["persona_id"]
    return (
        sub.assign(_level=levels_of_rows.to_numpy(), _persona=persona)
        .groupby(["_level", "leader"])
        .agg(n_pairs=("message_id", "size"), n_personas=("_persona", "nunique"), n_messages=("message_id", "nunique"))
    )


def unit(n: int, i: int) -> np.ndarray:
    w = np.zeros(n)
    w[i] = 1.0
    return w


def analyse(effects: pd.DataFrame, outcomes: Sequence[str]) -> Dict[str, pd.DataFrame]:
    codes_all = cluster_codes(effects)
    cell_rows: List[dict] = []
    contrast_rows: List[dict] = []
    placement_rows: List[dict] = []
    for framework, topic, mask in scopes(effects):
        sub = effects[mask]
        codes = {k: v[mask] for k, v in codes_all.items()}
        scope = {"framework": framework, "topic": topic}
        # "all" is the overall effect of each leader (the RQ1 estimate); then by group;
        # then by placement, for a figure, over all topics only.
        splits = {
            "all": pd.Series("all", index=sub.index),
            "group": sub["group"],
        }
        if topic == "all":
            splits["placement"] = sub["placement"]
        layouts = {name: cell_layout(levels, sub["leader"]) for name, levels in splits.items()}
        counts = {name: cell_counts(sub, levels) for name, levels in splits.items()}

        for outcome in outcomes:
            values = sub[outcome].to_numpy(dtype=float)
            for name, (labels, cells) in layouts.items():
                means, _, covs = cell_covariances(values, cells, len(labels), codes)
                for i, (level, leader) in enumerate(labels):
                    row = {**scope, "outcome": outcome, "leader": leader,
                           **combine(unit(len(labels), i), means, covs), **counts[name].loc[(level, leader)].to_dict()}
                    if name == "placement":
                        placement_rows.append({**row, "placement": int(level)})
                    else:
                        cell_rows.append({**row, "group": level})
                if name != "group":
                    continue
                index = {label: i for i, label in enumerate(labels)}
                groups = [g for g in GROUPS if (g, LEADERS[0]) in index]
                for a, b in LEADER_PAIRS:
                    for g in groups:
                        w = unit(len(labels), index[(g, a)]) - unit(len(labels), index[(g, b)])
                        contrast_rows.append({**scope, "outcome": outcome, "kind": "within_group", "group": g,
                                              "leader_a": a, "leader_b": b, **combine(w, means, covs)})
                    if "left" in groups and "right" in groups:
                        w = (unit(len(labels), index[("left", a)]) - unit(len(labels), index[("left", b)])
                             - unit(len(labels), index[("right", a)]) + unit(len(labels), index[("right", b)]))
                        contrast_rows.append({**scope, "outcome": outcome, "kind": "left_minus_right",
                                              "group": "left-right", "leader_a": a, "leader_b": b,
                                              **combine(w, means, covs)})
    stats = ["estimate", "se", "ci_low", "ci_high"]
    sizes = ["n_pairs", "n_personas", "n_messages"]
    scope_cols = ["framework", "topic", "outcome"]
    return {
        "group_effects": pd.DataFrame(cell_rows, columns=scope_cols + ["group", "leader"] + stats + sizes),
        "contrasts": pd.DataFrame(contrast_rows, columns=scope_cols + ["kind", "group", "leader_a", "leader_b"] + stats),
        "by_placement": pd.DataFrame(placement_rows, columns=scope_cols + ["placement", "leader"] + stats + sizes),
    }


def print_main(tables: Dict[str, pd.DataFrame]) -> None:
    names = {"pedro_sanchez": "Sánchez", "alberto_nunez_feijoo": "Feijóo", "santiago_abascal": "Abascal"}
    cells = tables["group_effects"]
    main = cells[(cells.framework == "all") & (cells.topic == "all") & (cells.outcome == "stance")]
    print("\nStance, attributed minus anonymous, both frameworks and all topics [95% CI]:")
    for g in ["all", *GROUPS]:
        rows = main[main.group == g]
        if rows.empty:
            continue
        cells_text = "  ".join(
            f"{names[r.leader]} {r.estimate:+.3f} [{r.ci_low:+.3f}, {r.ci_high:+.3f}]" for r in rows.itertuples()
        )
        print(f"  {g:<7} ({int(rows.n_personas.iloc[0])} personas)  {cells_text}")
    con = tables["contrasts"]
    con = con[(con.framework == "all") & (con.topic == "all") & (con.outcome == "stance") & (con.kind == "left_minus_right")]
    if not con.empty:
        print("Congruence, (A minus B among left) minus (A minus B among right); > 0 means congruent:")
        for r in con.itertuples():
            print(f"  {names[r.leader_a]} vs {names[r.leader_b]}: {r.estimate:+.3f} [{r.ci_low:+.3f}, {r.ci_high:+.3f}]")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    parser.add_argument("--frame", choices=sorted(QUESTIONS), default="spain",
                        help="Ideology elicitation to group by (default: spain).")
    parser.add_argument("--reactions-dir", type=Path, default=DEFAULT_REACTIONS_DIR)
    parser.add_argument("--ideology-dir", type=Path, default=DEFAULT_IDEOLOGY_DIR)
    parser.add_argument("--stimuli", type=Path, default=DEFAULT_STIMULI,
                        help="Stimuli table with keep_for_prompt; failed posts are dropped.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--outcomes", nargs="+", choices=OUTCOMES, default=OUTCOMES)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    slug = safe_slug(args.model)
    reactions = load_reactions(args.reactions_dir, slug)
    frameworks = sorted(reactions["framework"].unique())
    placements = load_placements(args.ideology_dir, slug, args.frame, frameworks)
    effects, counts = build_effects(reactions, load_kept_messages(args.stimuli), placements)

    print(f"{counts['posts_run']} posts run, {len(counts['posts_dropped_keep_for_prompt'])} dropped (keep_for_prompt false)")
    print(f"{counts['pairs_complete']} of {counts['pairs_expected']} persona-post pairs in all four conditions; "
          f"{counts['pairs_analysed']} analysed")
    if counts["personas_excluded_no_matching_placement"]:
        print(f"[WARNING] Personas without a placement for their text: {counts['personas_excluded_no_matching_placement']}")
    print(f"Personas by group: {counts['personas_by_group']}")

    tables = analyse(effects, args.outcomes)
    out_dir = args.output_dir / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        table.insert(0, "frame", args.frame)
        table.insert(0, "model", args.model)
        table.to_csv(out_dir / f"rq2_{name}_{args.frame}.csv", index=False)
    summary = {
        "model": args.model,
        "frame": args.frame,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "groups": {g: [min(v), max(v)] for g, v in GROUPS.items()},
        "leaders_left_to_right": LEADERS,
        "outcomes": list(args.outcomes),
        "effect": "attributed minus anonymous response of the same persona to the same post",
        "confidence_intervals": "95%, two-way cluster-robust by message and persona, normal approximation",
        **counts,
    }
    (out_dir / f"rq2_summary_{args.frame}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                                                            encoding="utf-8")
    print_main(tables)
    print(f"\nWrote {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
