import importlib.util
import json
from pathlib import Path
import sys

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


values = load_module("describe_persona_values", ROOT / "src" / "describe_persona_values.py")
SLUG = "openai_gpt-oss-20b"
MFQ = ["Care", "Equality", "Proportionality", "Loyalty", "Authority", "Purity"]


def write_inputs(tmp_path):
    """Two topics x three levels of two personas each. In MFT, topic t1 high and t2 low hold the
    same personas; placement rises with Proportionality and falls with Care."""
    selection, ideology = tmp_path / "selection", tmp_path / "ideology" / SLUG
    selection.mkdir()
    ideology.mkdir(parents=True)
    cells = {
        "iw": {("t1", "high"): ["a", "b"], ("t1", "mid"): ["c", "d"], ("t1", "low"): ["e", "f"],
               ("t2", "high"): ["a", "g"], ("t2", "mid"): ["c", "h"], ("t2", "low"): ["e", "i"]},
        "mft": {("t1", "high"): ["p", "q"], ("t1", "mid"): ["r", "s"], ("t1", "low"): ["t", "u"],
                ("t2", "high"): ["t", "u"], ("t2", "mid"): ["r", "s"], ("t2", "low"): ["p", "q"]},
    }
    placements = {"a": 2, "b": 3, "c": 5, "d": 6, "e": 8, "f": 9, "g": 4, "h": 5, "i": 10,
                  "p": 8, "q": 9, "r": 5, "s": 6, "t": 1, "u": 2}
    for framework, by_cell in cells.items():
        rows = []
        for (topic, level), ids in by_cell.items():
            for pid in ids:
                p = placements[pid]
                row = {"persona_id": pid, "topic": topic, "profile_level": level,
                       "profile_meaning": f"{topic} {level}", "surv-self": -p, "trad-sec": p % 3}
                row.update({f"mfq2_{name}": 3 for name in MFQ})
                row.update({"mfq2_Care": 11 - p, "mfq2_Proportionality": p})
                rows.append(row)
        pd.DataFrame(rows).to_csv(selection / f"{framework}_selected_topk.csv", index=False)
        ids = sorted({pid for group in by_cell.values() for pid in group})
        status = ["error" if pid == "i" else "success" for pid in ids]
        pd.DataFrame({"persona_id": ids, "status": status,
                      "left_right_self_placement": [None if s == "error" else placements[pid]
                                                    for pid, s in zip(ids, status)]}
                     ).to_csv(ideology / f"{framework}_spain.csv", index=False)
    return ["--selection-dir", str(selection), "--ideology-dir", str(tmp_path / "ideology"),
            "--output-dir", str(tmp_path / "out")]


def test_groups_correlations_and_shared_cells(tmp_path):
    assert values.main(write_inputs(tmp_path)) == 0
    out = tmp_path / "out" / SLUG
    groups = pd.read_csv(out / "profile_groups_spain.csv").set_index(["framework", "topic", "profile_level"])
    assert groups.loc[("iw", "t1", "high"), ["n_left", "n_centre", "n_right"]].tolist() == [2, 0, 0]
    assert groups.loc[("iw", "t1", "mid"), "pct_centre"] == 100
    # Persona i has no valid placement, so its slot is left out.
    assert groups.loc[("iw", "t2", "low"), "n_personas"] == 1
    assert groups.loc[("mft", "t2", "low"), "mean_placement"] == pytest.approx(8.5)
    assert list(groups.loc[("mft", "t1")].index) == ["high", "mid", "low"]

    corr = pd.read_csv(out / "value_correlations_spain.csv").set_index(["framework", "column"])
    assert corr.loc[("iw", "surv-self"), "rho"] == pytest.approx(-1)
    assert corr.loc[("iw", "surv-self"), "n"] == 8
    assert corr.loc[("mft", "mfq2_Care"), "rho"] == pytest.approx(-1)
    assert corr.loc[("mft", "mfq2_Proportionality"), "rho"] == pytest.approx(1)
    assert corr.loc[("mft", "mfq2_Loyalty")].isna()["rho"]  # constant column
    assert set(corr.loc["mft"].index) == {f"mfq2_{name}" for name in MFQ}

    summary = json.loads((out / "summary_spain.json").read_text())
    assert summary["slots_without_placement"] == {"iw": 1, "mft": 0}
    assert summary["unique_personas"] == {"iw": 8, "mft": 6}
    assert sorted(map(sorted, summary["cells_with_identical_personas"]["mft"])) == [
        ["t1:high", "t2:low"], ["t1:low", "t2:high"], ["t1:mid", "t2:mid"]]
    assert summary["cells_with_identical_personas"]["iw"] == []


def test_spearman_interval_contains_rho():
    x = pd.Series(range(50))
    y = pd.Series([v + (7 if v % 5 == 0 else 0) for v in range(50)])
    result = values.spearman(x, y)
    assert result["n"] == 50 and result["ci_low"] < result["rho"] < result["ci_high"] < 1
