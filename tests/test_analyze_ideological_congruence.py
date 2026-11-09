import importlib.util
from pathlib import Path
import sys

import numpy as np
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


rq2 = load_module("analyze_ideological_congruence", ROOT / "src" / "analyze_ideological_congruence.py")

SLUG = "openai_gpt-oss-20b"
CONDITIONS = ["anonymous", "pedro_sanchez", "alberto_nunez_feijoo", "santiago_abascal"]
NAMES = {-1: "reject", 0: "neutral", 1: "support"}
# Stance by persona and condition: the left persona supports Sánchez's name and
# rejects the others, the right persona the opposite, the centre one is neutral.
STANCES = {
    "L": {"anonymous": 0, "pedro_sanchez": 1, "alberto_nunez_feijoo": -1, "santiago_abascal": -1},
    "C": {"anonymous": 0, "pedro_sanchez": 0, "alberto_nunez_feijoo": 0, "santiago_abascal": 0},
    "R": {"anonymous": 0, "pedro_sanchez": -1, "alberto_nunez_feijoo": 1, "santiago_abascal": 1},
}
PLACEMENTS = {"L": 2, "C": 5, "R": 8}


def write_inputs(tmp_path, skip=(), sha_of=lambda pid: f"sha-{pid}"):
    """Reactions for 3 personas x 4 posts (m9 failed the checks), ideology and stimuli."""
    rows = []
    for pid, by_condition in STANCES.items():
        for message in ["m1", "m2", "m3", "m9"]:
            for condition in CONDITIONS:
                if (pid, message, condition) in skip:
                    continue
                stance = by_condition[condition]
                rows.append({
                    "framework": "iw", "topic": "corruption", "persona_id": pid, "message_id": message,
                    "speaker_condition": condition, "persona_sha256": f"sha-{pid}", "stance": NAMES[stance],
                    "agreement": 3 + stance, "legitimacy": 3 + stance, "offensiveness": 3 - stance,
                    "endorsement": 3 + stance, "brief_reason": "text",
                })
    table = pd.DataFrame(rows)
    for condition, part in table.groupby("speaker_condition"):
        folder = tmp_path / "reactions" / "run" / SLUG / "iw" / "corruption" / condition
        folder.mkdir(parents=True)
        part.to_csv(folder / "responses_active.csv", index=False)
    ideology = tmp_path / "ideology" / SLUG
    ideology.mkdir(parents=True)
    pd.DataFrame([
        {"framework": "iw", "frame": "spain", "persona_id": pid, "persona_sha256": sha_of(pid),
         "left_right_self_placement": placement, "status": "success"}
        for pid, placement in PLACEMENTS.items()
    ]).to_csv(ideology / "iw_spain.csv", index=False)
    stimuli = tmp_path / "x_stimuli.csv"
    pd.DataFrame({"message_id": ["m1", "m2", "m3", "m9"], "keep_for_prompt": ["True", "True", "True", "False"]}).to_csv(
        stimuli, index=False)
    return tmp_path / "reactions", tmp_path / "ideology", stimuli


def effects_for(tmp_path, **kwargs):
    reactions_dir, ideology_dir, stimuli = write_inputs(tmp_path, **kwargs)
    reactions = rq2.load_reactions(reactions_dir, SLUG)
    placements = rq2.load_placements(ideology_dir, SLUG, "spain", ["iw"])
    return rq2.build_effects(reactions, rq2.load_kept_messages(stimuli), placements)


def test_failed_posts_and_incomplete_pairs_are_dropped(tmp_path):
    effects, counts = effects_for(tmp_path, skip={("C", "m2", "santiago_abascal")})
    assert counts["posts_dropped_keep_for_prompt"] == ["m9"]
    assert (counts["pairs_expected"], counts["pairs_complete"], counts["pairs_analysed"]) == (9, 8, 8)
    assert len(effects) == 8 * 3
    assert "m9" not in set(effects["message_id"])
    assert counts["personas_by_group"] == {"iw": {"left": 1, "centre": 1, "right": 1}}


def test_group_effects_and_congruence(tmp_path):
    effects, _ = effects_for(tmp_path)
    tables = rq2.analyse(effects, ["stance", "offensiveness"])
    cells = tables["group_effects"]
    stance = cells[(cells.framework == "all") & (cells.topic == "all") & (cells.outcome == "stance")]
    got = {(r.group, r.leader): r.estimate for r in stance.itertuples()}
    assert got[("left", "pedro_sanchez")] == 1 and got[("left", "santiago_abascal")] == -1
    assert got[("right", "pedro_sanchez")] == -1 and got[("right", "alberto_nunez_feijoo")] == 1
    assert got[("centre", "pedro_sanchez")] == 0
    assert got[("all", "pedro_sanchez")] == 0  # the overall (RQ1) effect hides both groups
    assert set(stance.n_pairs) == {3, 9}

    con = tables["contrasts"]
    did = con[(con.framework == "all") & (con.topic == "all") & (con.kind == "left_minus_right")]
    got = {(r.outcome, r.leader_a, r.leader_b): r.estimate for r in did.itertuples()}
    assert got[("stance", "pedro_sanchez", "alberto_nunez_feijoo")] == 4
    assert got[("stance", "alberto_nunez_feijoo", "santiago_abascal")] == 0
    assert got[("offensiveness", "pedro_sanchez", "alberto_nunez_feijoo")] == -4
    within = con[(con.framework == "all") & (con.topic == "all") & (con.kind == "within_group")
                 & (con.group == "left") & (con.outcome == "stance") & (con.leader_a == "pedro_sanchez")]
    assert within.set_index("leader_b").loc["alberto_nunez_feijoo", "estimate"] == 2

    by_placement = tables["by_placement"]
    assert sorted(by_placement.placement.unique()) == [2, 5, 8]
    assert set(by_placement.topic) == {"all"}


def test_two_way_variance_matches_hand_computation():
    values = np.array([1.0, 0.0, 3.0, 2.0])
    codes = {"message": np.array([0, 0, 1, 1]), "persona": np.array([0, 1, 0, 1]), "pair": np.array([0, 1, 2, 3])}
    means, sizes, covs = rq2.cell_covariances(values, np.zeros(4, dtype=int), 1, codes)
    psi = (values - values.mean()) / 4

    def one_way(code):
        sums = np.array([psi[code == g].sum() for g in np.unique(code)])
        g = len(sums)
        return g / (g - 1) * (sums ** 2).sum()

    expected = one_way(codes["message"]) + one_way(codes["persona"]) - one_way(codes["pair"])
    result = rq2.combine(np.array([1.0]), means, covs)
    assert result["estimate"] == pytest.approx(1.5)
    assert result["se"] == pytest.approx(np.sqrt(expected))
    assert result["ci_high"] - result["ci_low"] == pytest.approx(2 * rq2.Z95 * np.sqrt(expected))


def test_negative_two_way_variance_falls_back_to_one_way():
    values = np.array([1.0, -1.0, -1.0, 1.0])
    codes = {"message": np.array([0, 0, 1, 1]), "persona": np.array([0, 1, 0, 1]), "pair": np.array([0, 1, 2, 3])}
    means, _, covs = rq2.cell_covariances(values, np.zeros(4, dtype=int), 1, codes)
    result = rq2.combine(np.array([1.0]), means, covs)
    assert result["se"] == 0 and not np.isnan(result["ci_low"])


def test_persona_whose_placement_is_for_another_text_is_left_out(tmp_path):
    effects, counts = effects_for(tmp_path, sha_of=lambda pid: "other" if pid == "C" else f"sha-{pid}")
    assert counts["personas_excluded_no_matching_placement"] == ["iw:C"]
    assert set(effects["persona_id"]) == {"L", "R"}


def test_unexpected_inputs_raise(tmp_path):
    reactions_dir, _, _ = write_inputs(tmp_path)
    path = next(reactions_dir.rglob("anonymous/responses_active.csv"))
    table = pd.read_csv(path)
    pd.concat([table, table.head(1)]).to_csv(path, index=False)
    with pytest.raises(ValueError, match="more than once"):
        rq2.load_reactions(reactions_dir, SLUG)
    table.assign(stance="maybe").to_csv(path, index=False)
    with pytest.raises(ValueError, match="Unknown stance"):
        rq2.load_reactions(reactions_dir, SLUG)
    with pytest.raises(FileNotFoundError):
        rq2.load_reactions(reactions_dir, "another_model")


def test_main_writes_the_tables(tmp_path, capsys):
    reactions_dir, ideology_dir, stimuli = write_inputs(tmp_path)
    out = tmp_path / "out"
    assert rq2.main(["--reactions-dir", str(reactions_dir), "--ideology-dir", str(ideology_dir),
                     "--stimuli", str(stimuli), "--output-dir", str(out)]) == 0
    folder = out / SLUG
    for name in ["group_effects", "contrasts", "by_placement"]:
        table = pd.read_csv(folder / f"rq2_{name}_spain.csv")
        assert set(table.model) == {"openai/gpt-oss-20b"} and set(table.frame) == {"spain"}
    assert (folder / "rq2_summary_spain.json").exists()
    printed = capsys.readouterr().out
    assert "1 dropped (keep_for_prompt false)" in printed
    assert "Sánchez vs Feijóo: +4.000" in printed
