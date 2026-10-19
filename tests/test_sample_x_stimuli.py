import csv
import importlib.util
import json
from pathlib import Path
import sys

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


sample = load_module("sample_x_stimuli", ROOT / "src" / "sample_x_stimuli.py")


def tables():
    stimuli, qa = [], []
    for actor, topic, n, excluded in [
        ("a", "corruption", 6, 1),
        ("b", "corruption", 2, 0),
        ("a", "other", 5, 0),
        ("b", "review_needed", 3, 0),
    ]:
        for i in range(n):
            message_id = f"{actor}_{topic}_{i}"
            stimuli.append({"message_id": message_id, "topic": topic, "text_anon": "t", "keep_for_prompt": "False", "is_morally_relevant": ""})
            qa.append({"message_id": message_id, "actor_id": actor, "exclude_reason": "too_short" if i < excluded else ""})
    return stimuli, qa


def test_eligible_drops_excluded_posts():
    stimuli, qa = tables()
    rows = sample.eligible(stimuli, qa, morally_relevant_only=False)
    assert len(rows) == len(stimuli) - 1
    assert "a_corruption_0" not in {row["message_id"] for row in rows}
    with pytest.raises(ValueError):
        sample.eligible(stimuli, qa, morally_relevant_only=True)


def test_draw_caps_cells_and_skips_other_by_default():
    stimuli, qa = tables()
    rows = sample.eligible(stimuli, qa, morally_relevant_only=False)
    drawn, available = sample.draw(rows, per_cell=3, other_per_author=0, seed=1)

    assert {cell: len(ids) for cell, ids in drawn.items()} == {("corruption", "a"): 3, ("corruption", "b"): 2}
    assert available[("corruption", "a")] == 5
    assert drawn == sample.draw(rows, per_cell=3, other_per_author=0, seed=1)[0]
    with_other, _ = sample.draw(rows, per_cell=3, other_per_author=2, seed=1)
    assert len(with_other[("other", "a")]) == 2


def test_model_calls():
    assert sample.model_calls(10, 50) == {"plausibility_check": 30, "persona_judgments": 2000}
    assert sample.model_calls(10, None)["persona_judgments"] is None


def test_main_sets_keep_for_prompt_and_writes_manifest(tmp_path):
    stimuli, qa = tables()
    paths = {"stimuli": tmp_path / "s.csv", "qa": tmp_path / "q.csv"}
    for key, rows in [("stimuli", stimuli), ("qa", qa)]:
        with paths[key].open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    manifest = tmp_path / "m.json"
    argv = ["--per-cell", "2", "--stimuli", str(paths["stimuli"]), "--qa", str(paths["qa"]), "--manifest", str(manifest)]

    assert sample.main(argv + ["--dry-run"]) == 0
    assert not manifest.exists()
    assert sample.main(argv) == 0
    with paths["stimuli"].open(encoding="utf-8", newline="") as handle:
        kept = [row["message_id"] for row in csv.DictReader(handle) if row["keep_for_prompt"] == "True"]
    written = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(kept) == written["posts"] == 4
    assert sorted(kept) == sorted(i for cell in written["cells"] for i in cell["message_ids"])
