#!/usr/bin/env python3
"""RQ4: compare the personas' leader preferences with CIS respondents'.

CIS side: in every barometer of the window (downloaded by
collect_cis_microdata.py), the weighted mean 1-10 rating of Sánchez, Feijóo and
Abascal, by left-right self-placement group (the cut-offs of the personas) and
by placement 1-10. Only respondents who place themselves and rate all three
leaders are used, so the leader means and the differences between leaders come
from the same people. Waves are pooled with equal weight, as in the topic
selection.

Persona side: the RQ2 speaker-attribution effects (analyze_ideological_congruence.py).

The comparison is about order and direction only: within each group, the order
of the three leaders and the sign of each difference between two leaders, and
the sign of the left-minus-right congruence contrast.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_ideological_congruence import LEADER_PAIRS, LEADERS, Z95, safe_slug
from elicit_persona_ideology import GROUPS, group_of


ROOT = Path(__file__).resolve().parents[1]
LEADER_NAMES = {
    "pedro_sanchez": "Pedro Sánchez",
    "alberto_nunez_feijoo": "Alberto Núñez Feijóo",
    "santiago_abascal": "Santiago Abascal",
}
SHORT = {"pedro_sanchez": "S", "alberto_nunez_feijoo": "F", "santiago_abascal": "A"}
RATING_PREFIX = "VALORALIDERES_"
IDEOLOGY = "ESCIDEOL"
WEIGHT_RE = re.compile(r"PESO(_V\d+)?", re.IGNORECASE)
VALID = range(1, 11)  # ratings and placements; 0 (does not know the leader), 98 and 99 are left out
PAIRS = [f"{a}-{b}" for a, b in LEADER_PAIRS]
ITEMS = LEADERS + PAIRS


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(text.lower().split())


def rating_columns(labels: dict) -> dict:
    """Map each leader to the column of its 1-10 rating, by the column label."""
    by_name = {normalise(name): leader for leader, name in LEADER_NAMES.items()}
    found = {}
    for column, label in labels.items():
        leader = by_name.get(normalise(label or ""))
        if column.upper().startswith(RATING_PREFIX) and leader:
            if leader in found:
                raise ValueError(f"Two rating columns for {leader}: {found[leader]} and {column}")
            found[leader] = column
    missing = [leader for leader in LEADERS if leader not in found]
    if missing:
        raise ValueError(f"No rating column for {missing}")
    return found


def weight_column(columns) -> str:
    found = [column for column in columns if WEIGHT_RE.fullmatch(column)]
    if len(found) != 1:
        raise ValueError(f"Expected one weight column (PESO), found {found or 'none'}")
    return found[0]


def load_wave(path: Path):
    """Respondents of one barometer who place themselves 1-10 and rate all three leaders."""
    import pyreadstat

    _, meta = pyreadstat.read_sav(str(path), metadataonly=True)
    ratings = rating_columns(meta.column_names_to_labels)
    if IDEOLOGY not in meta.column_names:
        raise ValueError(f"{path.name}: no {IDEOLOGY} column")
    weight = weight_column(meta.column_names)
    data, _ = pyreadstat.read_sav(str(path), usecols=[IDEOLOGY, weight, *ratings.values()])
    table = pd.DataFrame({"placement": data[IDEOLOGY], "weight": data[weight]})
    for leader, column in ratings.items():
        table[leader] = data[column]
    if table.weight.isna().any() or (table.weight <= 0).any():
        raise ValueError(f"{path.name}: missing or non-positive weights")
    placed = table.placement.isin(VALID)
    rated = table[LEADERS].isin(VALID).all(axis=1)
    kept = table[placed & rated].copy()
    kept["placement"] = kept.placement.astype(int)
    kept["group"] = kept.placement.map(group_of)
    counts = {
        "respondents": len(table), "no_placement": int((~placed).sum()),
        "placed_not_rating_all_three": int((placed & ~rated).sum()), "analysed": len(kept),
        "weight_column": weight, "rating_columns": ratings,
    }
    return kept, counts


def weighted_mean(values: np.ndarray, weights: np.ndarray):
    """Weighted mean and its linearised standard error."""
    total = weights.sum()
    mean = float((weights * values).sum() / total)
    n = len(values)
    if n < 2:
        return mean, float("nan")
    variance = n / (n - 1) * float((weights ** 2 * (values - mean) ** 2).sum()) / total ** 2
    return mean, float(np.sqrt(variance))


def units(table: pd.DataFrame):
    yield "group", "all", table
    for name in GROUPS:
        part = table[table.group == name]
        if len(part):
            yield "group", name, part
    for placement, part in table.groupby("placement"):
        yield "placement", str(placement), part


def wave_estimates(table: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for level, unit, part in units(table):
        weights = part.weight.to_numpy(float)
        for item in ITEMS:
            if item in LEADERS:
                values = part[item].to_numpy(float)
            else:
                a, b = item.split("-")
                values = part[a].to_numpy(float) - part[b].to_numpy(float)
            estimate, se = weighted_mean(values, weights)
            rows.append({"level": level, "unit": unit, "item": item, "estimate": estimate, "se": se, "n": len(part)})
    return pd.DataFrame(rows)


def pool(waves: pd.DataFrame) -> pd.DataFrame:
    """Mean over waves, each wave weighing the same; waves are independent samples."""
    pooled = waves.groupby(["level", "unit", "item"], sort=False).agg(
        estimate=("estimate", "mean"), se=("se", lambda s: float(np.sqrt((s ** 2).sum(min_count=len(s)))) / len(s)),
        n=("n", "sum"), n_waves=("study_id", "nunique")).reset_index()
    pooled["ci_low"] = pooled.estimate - Z95 * pooled.se
    pooled["ci_high"] = pooled.estimate + Z95 * pooled.se
    return pooled


def cis_congruence(pooled: pd.DataFrame) -> pd.DataFrame:
    groups = pooled[(pooled.level == "group") & pooled.item.isin(PAIRS)].set_index(["unit", "item"])
    rows = []
    for item in PAIRS:
        left, right = groups.loc[("left", item)], groups.loc[("right", item)]
        se = float(np.hypot(left.se, right.se))
        estimate = float(left.estimate - right.estimate)
        rows.append({"item": item, "estimate": estimate, "se": se,
                     "ci_low": estimate - Z95 * se, "ci_high": estimate + Z95 * se})
    return pd.DataFrame(rows)


def order(means: dict) -> str:
    return " > ".join(SHORT[leader] for leader in sorted(means, key=means.get, reverse=True))


def load_persona_tables(rq2_dir: Path, frame: str, outcome: str):
    def read(name):
        table = pd.read_csv(rq2_dir / f"rq2_{name}_{frame}.csv")
        return table[(table.framework == "all") & (table.topic == "all") & (table.outcome == outcome)]

    return read("group_effects"), read("contrasts"), read("by_placement")


def wave_orders(waves: pd.DataFrame) -> pd.DataFrame:
    """Order of the three leaders in each wave and unit."""
    means = waves[waves.item.isin(LEADERS)].pivot_table(
        index=["level", "unit", "study_id"], columns="item", values="estimate")
    return means.apply(lambda row: order(row.to_dict()), axis=1).rename("order").reset_index()


def compare(pooled: pd.DataFrame, congruence: pd.DataFrame, persona_groups, persona_contrasts, persona_placement,
            waves: pd.DataFrame):
    """Order and direction of the leader preferences, CIS against personas."""
    cis = pooled.set_index(["level", "unit", "item"])
    by_wave = wave_orders(waves)
    orderings = []
    for level, unit in pooled[["level", "unit"]].drop_duplicates().itertuples(index=False):
        if level == "group":
            persona = persona_groups[persona_groups.group == unit].set_index("leader")
            n_personas = int(persona.n_personas.iloc[0]) if len(persona) else 0
        else:
            persona = persona_placement[persona_placement.placement == int(unit)].set_index("leader")
            n_personas = int(persona.n_personas.iloc[0]) if len(persona) else 0
        cis_means = {leader: float(cis.loc[(level, unit, leader), "estimate"]) for leader in LEADERS}
        row = {"level": level, "unit": unit, "n_respondents": int(cis.loc[(level, unit, LEADERS[0]), "n"]),
               "n_personas": n_personas, "cis_order": order(cis_means)}
        row.update({f"cis_{SHORT[leader]}": value for leader, value in cis_means.items()})
        if len(persona) == len(LEADERS):
            effects = {leader: float(persona.loc[leader, "estimate"]) for leader in LEADERS}
            row["persona_order"] = order(effects)
            row.update({f"persona_{SHORT[leader]}": value for leader, value in effects.items()})
            row["same_order"] = row["cis_order"] == row["persona_order"]
            orders = by_wave[(by_wave.level == level) & (by_wave.unit == unit)].order
            row["waves_same_order"] = int((orders == row["persona_order"]).sum())
            row["n_waves"] = len(orders)
        orderings.append(row)
    orderings = pd.DataFrame(orderings)
    for column in ("waves_same_order", "n_waves"):
        if column in orderings:
            orderings[column] = orderings[column].astype("Int64")

    within = persona_contrasts[persona_contrasts.kind == "within_group"].set_index(["group", "leader_a", "leader_b"])
    across = persona_contrasts[persona_contrasts.kind == "left_minus_right"].set_index(["leader_a", "leader_b"])
    rows = []
    for item in PAIRS:
        a, b = item.split("-")
        for unit in GROUPS:
            if ("group", unit, item) not in cis.index or (unit, a, b) not in within.index:
                continue
            rows.append(pair_row("within_group", unit, a, b, cis.loc[("group", unit, item)], within.loc[(unit, a, b)]))
        if (a, b) in across.index:
            rows.append(pair_row("left_minus_right", "", a, b, congruence.set_index("item").loc[item], across.loc[(a, b)]))
    return orderings, pd.DataFrame(rows)


def pair_row(kind, group, a, b, cis, persona):
    def excludes_zero(row):
        return bool(row.ci_low > 0 or row.ci_high < 0)

    return {
        "kind": kind, "group": group, "leader_a": a, "leader_b": b,
        "cis_estimate": float(cis.estimate), "cis_ci_low": float(cis.ci_low), "cis_ci_high": float(cis.ci_high),
        "persona_estimate": float(persona.estimate), "persona_ci_low": float(persona.ci_low),
        "persona_ci_high": float(persona.ci_high),
        "same_direction": bool(np.sign(cis.estimate) == np.sign(persona.estimate)),
        "cis_excludes_zero": excludes_zero(cis), "persona_excludes_zero": excludes_zero(persona),
    }


def print_main(orderings: pd.DataFrame, pairs: pd.DataFrame, n_waves: int) -> None:
    print(f"\nOrder of the leaders by group (CIS: mean rating over {n_waves} waves; personas: attribution effect)")
    groups = orderings[orderings.level == "group"]
    for row in groups.itertuples():
        print(f"  {row.unit:<7} CIS {row.cis_order:<10} personas {row.persona_order:<10}"
              f" {'same' if row.same_order else 'different':<9} (same in {row.waves_same_order} of {row.n_waves} waves)")
    print("\nDifferences between leaders (A minus B; CIS rating points, persona effect on stance)")
    for row in pairs.itertuples():
        where = row.group or "left - right"
        print(f"  {where:<13} {SHORT[row.leader_a]}-{SHORT[row.leader_b]}  CIS {row.cis_estimate:+.2f}"
              f" [{row.cis_ci_low:+.2f}, {row.cis_ci_high:+.2f}]  personas {row.persona_estimate:+.3f}"
              f" [{row.persona_ci_low:+.3f}, {row.persona_ci_high:+.3f}]  {'same' if row.same_direction else 'OPPOSITE'}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=ROOT / "data" / "raw" / "cis_microdata" / "manifest.csv")
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    parser.add_argument("--frame", default="spain")
    parser.add_argument("--outcome", default="stance")
    parser.add_argument("--rq2-dir", type=Path, default=ROOT / "data" / "rq2_ideological_congruence")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "rq4_cis_comparison")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    manifest = pd.read_csv(args.manifest, dtype={"study_id": str})
    waves, counts = [], {}
    for wave in manifest.itertuples():
        path = Path(wave.sav_file)
        path = path if path.is_absolute() else ROOT / path
        table, counts[wave.study_id] = load_wave(path)
        estimates = wave_estimates(table)
        estimates.insert(0, "study_id", wave.study_id)
        estimates.insert(1, "year", wave.year)
        estimates.insert(2, "month", wave.month)
        waves.append(estimates)
    waves = pd.concat(waves, ignore_index=True)
    pooled = pool(waves)
    congruence = cis_congruence(pooled)

    slug = safe_slug(args.model)
    persona_groups, persona_contrasts, persona_placement = load_persona_tables(
        args.rq2_dir / slug, args.frame, args.outcome)
    orderings, pairs = compare(pooled, congruence, persona_groups, persona_contrasts, persona_placement, waves)

    out = args.output_dir
    (out / slug).mkdir(parents=True, exist_ok=True)
    waves.to_csv(out / "cis_leader_ratings_by_wave.csv", index=False)
    pooled.to_csv(out / "cis_leader_ratings_pooled.csv", index=False)
    congruence.to_csv(out / "cis_congruence_pooled.csv", index=False)
    for table in (orderings, pairs):
        table.insert(0, "model", args.model)
        table.insert(1, "frame", args.frame)
        table.insert(2, "outcome", args.outcome)
    orderings.to_csv(out / slug / f"rq4_orderings_{args.frame}.csv", index=False)
    pairs.to_csv(out / slug / f"rq4_pairs_{args.frame}.csv", index=False)

    groups = orderings[orderings.level == "group"]
    within = pairs[pairs.kind == "within_group"]
    summary = {
        "model": args.model, "frame": args.frame, "outcome": args.outcome,
        "waves": [f"{w.study_id} ({w.year}-{int(w.month):02d})" for w in manifest.itertuples()],
        "respondents": {key: value["respondents"] for key, value in counts.items()},
        "respondents_analysed": int(sum(value["analysed"] for value in counts.values())),
        "respondents_total": int(sum(value["respondents"] for value in counts.values())),
        "excluded_no_placement": int(sum(value["no_placement"] for value in counts.values())),
        "excluded_not_rating_all_three": int(sum(value["placed_not_rating_all_three"] for value in counts.values())),
        "weight_columns": {key: value["weight_column"] for key, value in counts.items() if value["weight_column"] != "PESO"},
        "rating_columns_not_default": {key: value["rating_columns"] for key, value in counts.items()
                                       if value["rating_columns"] != {"pedro_sanchez": "VALORALIDERES_1",
                                                                       "alberto_nunez_feijoo": "VALORALIDERES_2",
                                                                       "santiago_abascal": "VALORALIDERES_4"}},
        "groups_same_order": f"{int(groups[groups.unit != 'all'].same_order.sum())} of {len(groups[groups.unit != 'all'])}",
        "pairs_same_direction": f"{int(within.same_direction.sum())} of {len(within)}",
        "congruence_same_direction": f"{int(pairs[pairs.kind == 'left_minus_right'].same_direction.sum())} of "
                                     f"{len(pairs[pairs.kind == 'left_minus_right'])}",
    }
    (out / slug / f"rq4_summary_{args.frame}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")

    print(f"{len(manifest)} waves, {summary['respondents_analysed']:,} of {summary['respondents_total']:,} respondents "
          f"({summary['excluded_no_placement']:,} without a placement, "
          f"{summary['excluded_not_rating_all_three']:,} not rating all three leaders)")
    print_main(orderings, pairs, len(manifest))
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
