#!/usr/bin/env python3
"""Relate the personas' value profiles to their elicited left-right self-placement.

The personas were selected by their Inglehart-Welzel (IW) or Moral Foundations
(MFT) profile, and no prompt mentions ideology, so the left, centre and right
groups of RQ2 and RQ4 come from those profiles. This script shows how:

* For each framework, topic and profile level (high, mid, low; 50 personas
  each), the share of personas in each ideological group and the mean
  self-placement. A persona selected for several topics counts in each.
* For each framework, the Spearman correlation between the self-placement and
  each value dimension shown to the model in that framework: the two IW
  coordinates, or the six MFQ-2 foundation scores. Each persona counts once.
* Cells that hold exactly the same personas, which are not independent.

Run after elicit_persona_ideology.py. Writes to data/persona_values/<model>/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from analyze_ideological_congruence import safe_slug
from elicit_persona_ideology import DEFAULT_SELECTION, FIELD, GROUPS, QUESTIONS, group_of

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IDEOLOGY_DIR = ROOT / "data" / "persona_ideology"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "persona_values"
FRAMEWORKS = ["iw", "mft"]
LEVELS = ["high", "mid", "low"]
# The value dimensions each framework puts in the persona prompt.
DIMENSIONS = {
    "iw": {"surv-self": "Survival/self-expression", "trad-sec": "Traditional/secular-rational"},
    "mft": {f"mfq2_{name}": name for name in ["Care", "Equality", "Proportionality", "Loyalty", "Authority", "Purity"]},
}
Z95 = 1.959963984540054


def load(selection_dir: Path, ideology_dir: Path, slug: str, frame: str, framework: str) -> tuple[pd.DataFrame, int]:
    """Selected persona slots of one framework with their placement; also the number of slots without one."""
    selection_path = selection_dir / f"{framework}_selected_topk.csv"
    ideology_path = ideology_dir / slug / f"{framework}_{frame}.csv"
    for path in (selection_path, ideology_path):
        if not path.exists():
            raise FileNotFoundError(f"{path} is missing")
    selection = pd.read_csv(selection_path, dtype={"persona_id": str})
    ideology = pd.read_csv(ideology_path, dtype={"persona_id": str})
    ideology = ideology.loc[ideology["status"] == "success", ["persona_id", FIELD]]
    table = selection.merge(ideology, on="persona_id", how="left", validate="many_to_one")
    missing = int(table[FIELD].isna().sum())
    table = table.dropna(subset=[FIELD]).copy()
    table["placement"] = table[FIELD].astype(int)
    table["group"] = table["placement"].map(group_of)
    table["framework"] = framework
    return table, missing


def profile_groups(table: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (framework, topic, level), cell in table.groupby(["framework", "topic", "profile_level"]):
        counts = cell["group"].value_counts().reindex(list(GROUPS), fill_value=0)
        row = {"framework": framework, "topic": topic, "profile_level": level,
               "profile_meaning": cell["profile_meaning"].iloc[0], "n_personas": len(cell)}
        row.update({f"n_{g}": int(counts[g]) for g in GROUPS})
        row.update({f"pct_{g}": 100 * counts[g] / len(cell) for g in GROUPS})
        row["mean_placement"] = cell["placement"].mean()
        rows.append(row)
    out = pd.DataFrame(rows)
    out["level_order"] = out["profile_level"].map(LEVELS.index)
    return out.sort_values(["framework", "topic", "level_order"]).drop(columns="level_order").reset_index(drop=True)


def spearman(x: pd.Series, y: pd.Series) -> Dict[str, float]:
    """Spearman's rho with a 95% CI from Fisher's z, using the Fieller et al. (1957) variance 1.06/(n-3)."""
    rx, ry = x.rank(), y.rank()
    n = len(x)
    if n < 4 or rx.nunique() < 2 or ry.nunique() < 2:
        return {"n": n, "rho": np.nan, "ci_low": np.nan, "ci_high": np.nan}
    rho = float(np.corrcoef(rx, ry)[0, 1])
    z, se = np.arctanh(rho), np.sqrt(1.06 / (n - 3))
    return {"n": n, "rho": rho, "ci_low": float(np.tanh(z - Z95 * se)), "ci_high": float(np.tanh(z + Z95 * se))}


def value_correlations(table: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for framework, personas in table.groupby("framework"):
        personas = personas.drop_duplicates("persona_id")
        for column, label in DIMENSIONS[framework].items():
            values = pd.to_numeric(personas[column], errors="raise")
            rows.append({"framework": framework, "dimension": label, "column": column,
                         **spearman(values, personas["placement"])})
    return pd.DataFrame(rows)


def shared_cells(table: pd.DataFrame) -> Dict[str, List[List[str]]]:
    """For each framework, the sets of topic-level cells that hold exactly the same personas."""
    out = {}
    for framework, rows in table.groupby("framework"):
        by_set: Dict[frozenset, List[str]] = {}
        for (topic, level), cell in rows.groupby(["topic", "profile_level"]):
            by_set.setdefault(frozenset(cell["persona_id"]), []).append(f"{topic}:{level}")
        out[framework] = [sorted(cells) for cells in by_set.values() if len(cells) > 1]
    return out


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    parser.add_argument("--frame", choices=sorted(QUESTIONS), default="spain")
    parser.add_argument("--selection-dir", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--ideology-dir", type=Path, default=DEFAULT_IDEOLOGY_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    slug = safe_slug(args.model)
    tables, missing = [], {}
    for framework in FRAMEWORKS:
        table, missing[framework] = load(args.selection_dir, args.ideology_dir, slug, args.frame, framework)
        tables.append(table)
    table = pd.concat(tables, ignore_index=True)
    groups = profile_groups(table)
    correlations = value_correlations(table)
    shared = shared_cells(table)

    out_dir = args.output_dir / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    groups.to_csv(out_dir / f"profile_groups_{args.frame}.csv", index=False)
    correlations.to_csv(out_dir / f"value_correlations_{args.frame}.csv", index=False)
    summary = {
        "model": args.model, "frame": args.frame,
        "groups": {g: [min(v), max(v)] for g, v in GROUPS.items()},
        "persona_slots": {fw: int((table["framework"] == fw).sum()) for fw in FRAMEWORKS},
        "unique_personas": {fw: int(table.loc[table["framework"] == fw, "persona_id"].nunique()) for fw in FRAMEWORKS},
        "slots_without_placement": missing,
        "cells_with_identical_personas": shared,
    }
    (out_dir / f"summary_{args.frame}.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    shown = groups.assign(**{f"pct_{g}": groups[f"pct_{g}"].round(0).astype(int) for g in GROUPS},
                          mean_placement=groups["mean_placement"].round(1))
    with pd.option_context("display.width", 200, "display.max_colwidth", 50):
        print(shown[["framework", "topic", "profile_level", *[f"pct_{g}" for g in GROUPS], "mean_placement",
                     "profile_meaning"]].to_string(index=False))
        print("\nSpearman correlation with the self-placement (one row per persona):")
        print(correlations.round(2).to_string(index=False))
    for framework, cells in shared.items():
        for same in cells:
            print(f"{framework}: same personas in {', '.join(same)}")
    if any(missing.values()):
        print(f"Persona slots without a placement, left out: {missing}")
    print(f"\nWrote {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
