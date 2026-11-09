import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

pyreadstat = pytest.importorskip("pyreadstat")


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rq4 = load_module("analyze_cis_comparison", ROOT / "src" / "analyze_cis_comparison.py")

S, F, A = "pedro_sanchez", "alberto_nunez_feijoo", "santiago_abascal"
SLUG = "openai_gpt-oss-20b"
# placement, weight, Sánchez, Feijóo, Abascal. The last three are left out: no
# placement (98), no rating of Feijóo, and Feijóo unknown (0).
RESPONDENTS = [
    (2, 1.0, 8, 3, 1), (3, 3.0, 6, 3, 2), (5, 1.0, 4, 5, 3), (8, 2.0, 2, 7, 6), (9, 1.0, 1, 6, 7),
    (98, 1.0, 5, 5, 5), (4, 1.0, 7, np.nan, 1), (4, 1.0, 7, 0, 1),
]


def write_wave(path, weight_name="PESO", abascal_column="VALORALIDERES_4", shift=0.0):
    table = pd.DataFrame(RESPONDENTS, columns=["ESCIDEOL", weight_name, "S", "F", "A"])
    data = pd.DataFrame({
        "ESCIDEOL": table.ESCIDEOL, weight_name: table[weight_name],
        "VALORALIDERES_1": table.S + shift, "VALORALIDERES_2": table.F, "VALORALIDERES_3": 5.0,
    })
    data[abascal_column] = table.A
    if abascal_column == "VALORALIDERES_3":
        data["VALORALIDERES_4"] = 5.0
    labels = {"ESCIDEOL": "Escala de autoubicación ideológica (1-10)", weight_name: "Ponderación",
              "VALORALIDERES_1": "Pedro Sánchez", "VALORALIDERES_2": "Alberto Núñez Feijóo",
              "VALORALIDERES_3": "Yolanda Díaz", "VALORALIDERES_4": "Yolanda Díaz"}
    labels[abascal_column] = "Santiago Abascal"
    pyreadstat.write_sav(data, str(path), column_labels=[labels[column] for column in data.columns])


def test_rating_and_weight_columns_are_found_by_label_and_name():
    labels = {"VALORALIDERES_1": "Pedro Sánchez", "VALORALIDERES_2": "Alberto Núñez Feijóo",
              "VALORALIDERES_3": "Santiago Abascal", "VALORALIDERES_4": "Yolanda Díaz", "CONOMINIS_1": "Pedro Sánchez"}
    assert rq4.rating_columns(labels) == {S: "VALORALIDERES_1", F: "VALORALIDERES_2", A: "VALORALIDERES_3"}
    with pytest.raises(ValueError, match="No rating column"):
        rq4.rating_columns({"VALORALIDERES_1": "Pedro Sánchez"})
    with pytest.raises(ValueError, match="Two rating columns"):
        rq4.rating_columns({**labels, "VALORALIDERES_9": "Santiago  Abascal"})
    assert rq4.weight_column(["ESCIDEOL", "PESO_V2", "PESOCCAA"]) == "PESO_V2"
    with pytest.raises(ValueError, match="one weight column"):
        rq4.weight_column(["PESO", "PESO_V2"])


def test_wave_keeps_placed_respondents_who_rate_all_three(tmp_path):
    write_wave(tmp_path / "w.sav", weight_name="PESO_V2", abascal_column="VALORALIDERES_3")
    table, counts = rq4.load_wave(tmp_path / "w.sav")
    assert (counts["respondents"], counts["no_placement"], counts["placed_not_rating_all_three"],
            counts["analysed"]) == (8, 1, 2, 5)
    assert counts["weight_column"] == "PESO_V2" and counts["rating_columns"][A] == "VALORALIDERES_3"
    assert list(table.group) == ["left", "left", "centre", "right", "right"]

    estimates = rq4.wave_estimates(table).set_index(["level", "unit", "item"]).estimate
    assert estimates[("group", "left", S)] == pytest.approx((8 * 1 + 6 * 3) / 4)
    assert estimates[("group", "right", A)] == pytest.approx((6 * 2 + 7 * 1) / 3)
    assert estimates[("group", "left", f"{S}-{F}")] == pytest.approx(6.5 - 3)
    assert estimates[("placement", "9", F)] == 6


def test_weighted_mean_standard_error_and_equal_weight_pooling():
    values, weights = np.array([1.0, 3.0, 4.0]), np.array([1.0, 2.0, 1.0])
    mean, se = rq4.weighted_mean(values, weights)
    assert mean == pytest.approx(11 / 4)
    expected = 3 / 2 * ((weights ** 2) * (values - 11 / 4) ** 2).sum() / 16
    assert se == pytest.approx(np.sqrt(expected))

    waves = pd.DataFrame({"study_id": ["1", "2"], "level": "group", "unit": "left", "item": S,
                          "estimate": [6.0, 4.0], "se": [0.3, 0.4], "n": [1000, 10]})
    pooled = rq4.pool(waves).iloc[0]
    assert pooled.estimate == 5.0  # each wave weighs the same, whatever its size
    assert pooled.se == pytest.approx(0.25) and pooled.n == 1010 and pooled.n_waves == 2
    assert np.isnan(rq4.pool(waves.assign(se=[0.3, np.nan])).iloc[0].se)  # a wave without an SE is not dropped


def persona_tables(folder):
    rows = {"left": {S: -0.05, F: -0.10, A: -0.30}, "centre": {S: -0.10, F: -0.12, A: -0.20},
            "right": {S: -0.13, F: -0.05, A: -0.07}, "all": {S: -0.10, F: -0.10, A: -0.19}}
    base = {"model": "openai/gpt-oss-20b", "frame": "spain", "framework": "all", "topic": "all", "outcome": "stance"}
    groups = pd.DataFrame([{**base, "group": g, "leader": leader, "estimate": value, "se": 0.01,
                            "ci_low": value - 0.02, "ci_high": value + 0.02, "n_pairs": 9, "n_personas": 3,
                            "n_messages": 3} for g, by in rows.items() for leader, value in by.items()])
    contrasts = []
    for a, b in [(S, F), (S, A), (F, A)]:
        for g in ["left", "centre", "right"]:
            value = rows[g][a] - rows[g][b]
            contrasts.append({**base, "kind": "within_group", "group": g, "leader_a": a, "leader_b": b,
                              "estimate": value, "se": 0.01, "ci_low": value - 0.02, "ci_high": value + 0.02})
        value = (rows["left"][a] - rows["left"][b]) - (rows["right"][a] - rows["right"][b])
        contrasts.append({**base, "kind": "left_minus_right", "group": "", "leader_a": a, "leader_b": b,
                          "estimate": value, "se": 0.01, "ci_low": value - 0.02, "ci_high": value + 0.02})
    placement = pd.DataFrame([{**base, "placement": 2, "leader": leader, "estimate": value, "se": 0.01,
                               "ci_low": value - 0.02, "ci_high": value + 0.02, "n_pairs": 3, "n_personas": 1,
                               "n_messages": 3} for leader, value in rows["left"].items()])
    folder.mkdir(parents=True)
    groups.to_csv(folder / "rq2_group_effects_spain.csv", index=False)
    pd.DataFrame(contrasts).to_csv(folder / "rq2_contrasts_spain.csv", index=False)
    placement.to_csv(folder / "rq2_by_placement_spain.csv", index=False)


def test_main_compares_orders_and_directions(tmp_path, capsys):
    write_wave(tmp_path / "3571.sav")
    write_wave(tmp_path / "3577.sav", weight_name="PESO_V2", shift=1.0)
    pd.DataFrame({"study_id": ["3571", "3577"], "year": [2026, 2026], "month": [7, 9],
                  "sav_file": [str(tmp_path / "3571.sav"), str(tmp_path / "3577.sav")]}).to_csv(
        tmp_path / "manifest.csv", index=False)
    persona_tables(tmp_path / "rq2" / SLUG)
    out = tmp_path / "out"
    assert rq4.main(["--manifest", str(tmp_path / "manifest.csv"), "--rq2-dir", str(tmp_path / "rq2"),
                     "--output-dir", str(out)]) == 0

    pooled = pd.read_csv(out / "cis_leader_ratings_pooled.csv").set_index(["level", "unit", "item"])
    assert pooled.loc[("group", "left", S), "estimate"] == pytest.approx(6.5 + 0.5)  # mean of the two waves
    assert pooled.loc[("group", "left", S), "n_waves"] == 2

    orderings = pd.read_csv(out / SLUG / "rq4_orderings_spain.csv").set_index(["level", "unit"])
    assert orderings.loc[("group", "left"), "cis_order"] == "S > F > A"
    assert bool(orderings.loc[("group", "left"), "same_order"])
    assert orderings.loc[("group", "centre"), "cis_order"] == "F > S > A"
    assert not orderings.loc[("group", "centre"), "same_order"]
    assert orderings.loc[("group", "right"), "waves_same_order"] == 2

    pairs = pd.read_csv(out / SLUG / "rq4_pairs_spain.csv")
    opposite = pairs[~pairs.same_direction]
    assert list(zip(opposite.kind, opposite.group, opposite.leader_a, opposite.leader_b)) == [
        ("within_group", "centre", S, F)]
    assert set(pairs[pairs.kind == "left_minus_right"].same_direction) == {True}

    summary = json.loads((out / SLUG / "rq4_summary_spain.json").read_text())
    assert summary["respondents_analysed"] == 10 and summary["weight_columns"] == {"3577": "PESO_V2"}
    assert summary["pairs_same_direction"] == "8 of 9"
    assert "same in 2 of 2 waves" in capsys.readouterr().out
