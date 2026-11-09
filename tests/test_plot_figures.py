import importlib.util
from pathlib import Path
import sys

import pandas as pd
import pytest

pytest.importorskip("matplotlib")


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


by_placement = load_module("plot_by_placement", ROOT / "src" / "plot_by_placement.py")
over_time = load_module("plot_cis_over_time", ROOT / "src" / "plot_cis_over_time.py")
LEADERS = ["pedro_sanchez", "alberto_nunez_feijoo", "santiago_abascal"]


def test_placement_figure_with_and_without_the_cis_panel(tmp_path, capsys):
    folder = tmp_path / "rq2" / "openai_gpt-oss-20b"
    folder.mkdir(parents=True)
    pd.DataFrame([{"framework": "all", "outcome": "stance", "placement": p, "leader": leader,
                   "estimate": -0.1 * i + 0.01 * p, "ci_low": -0.1 * i, "ci_high": -0.1 * i + 0.1,
                   "n_personas": 1 if p == 1 else 20} for p in range(1, 11) for i, leader in enumerate(LEADERS)]
                 ).to_csv(folder / "rq2_by_placement_spain.csv", index=False)
    cis = tmp_path / "cis.csv"
    pd.DataFrame([{"level": "placement", "unit": str(p), "item": leader, "estimate": 2 + i + 0.3 * p,
                   "ci_low": 1.9 + i + 0.3 * p, "ci_high": 2.1 + i + 0.3 * p, "n": 1500}
                  for p in range(1, 11) for i, leader in enumerate(LEADERS)]).to_csv(cis, index=False)

    args = ["--input-dir", str(tmp_path / "rq2"), "--cis-ratings", str(cis)]
    assert by_placement.main(args + ["--output", str(tmp_path / "both.pdf")]) == 0
    assert by_placement.main(args + ["--no-cis", "--output", str(tmp_path / "personas.pdf")]) == 0
    for name in ["both", "personas"]:
        assert (tmp_path / f"{name}.pdf").stat().st_size > 0 and (tmp_path / f"{name}.png").exists()
    assert "not drawn (fewer than 2 personas): [1]" in capsys.readouterr().out


def test_over_time_figure(tmp_path):
    waves = tmp_path / "waves.csv"
    pd.DataFrame([{"level": "group", "unit": "right", "item": "alberto_nunez_feijoo-santiago_abascal",
                   "year": 2025, "month": m, "estimate": 1 - 0.2 * m, "se": 0.1} for m in range(1, 7)]
                 ).to_csv(waves, index=False)
    assert over_time.main(["--input", str(waves), "--output", str(tmp_path / "trend.pdf")]) == 0
    assert (tmp_path / "trend.pdf").exists()
    with pytest.raises(ValueError, match="No rows"):
        over_time.main(["--input", str(waves), "--group", "left"])
