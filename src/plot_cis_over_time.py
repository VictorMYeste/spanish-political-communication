"""Plot a CIS difference between two leaders, wave by wave, within one ideological group (RQ4).

Reads cis_leader_ratings_by_wave.csv (analyze_cis_comparison.py). By default it
draws the difference between the ratings of Feijóo and Abascal among
right-leaning respondents, the preference that changes during the window.
"""

import argparse
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from analyze_ideological_congruence import Z95  # noqa: E402
from plot_by_placement import AXIS, GRID, INK, MUTED, STYLE, save, setup_style  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


def load(path, group, leader_a, leader_b):
    table = pd.read_csv(path)
    item = f"{leader_a}-{leader_b}"
    table = table[(table.level == "group") & (table.unit == group) & (table["item"] == item)].copy()
    if table.empty:
        raise ValueError(f"No rows for group={group}, item={item} in {path}")
    table["date"] = pd.to_datetime(dict(year=table.year, month=table.month, day=15))
    table["ci_low"] = table.estimate - Z95 * table.se
    table["ci_high"] = table.estimate + Z95 * table.se
    return table.sort_values("date")


def plot(table, group, leader_a, leader_b, output):
    setup_style()
    a, b = STYLE[leader_a]["label"], STYLE[leader_b]["label"]
    fig, ax = plt.subplots(figsize=(3.05, 1.9))
    ax.fill_between(table.date, table.ci_low, table.ci_high, color=GRID, lw=0, zorder=1)
    ax.plot(table.date, table.estimate, color=INK, lw=1.2, zorder=3)
    ax.plot(table.date, table.estimate, linestyle="none", marker="o", markersize=2.8, markerfacecolor=INK,
            markeredgecolor="white", markeredgewidth=0.4, zorder=4)
    ax.axhline(0, color=AXIS, lw=0.8, zorder=2)
    ax.text(0.01, 0.97, f"{a} rated higher", transform=ax.transAxes, ha="left", va="top", color=MUTED, fontsize=7)
    ax.text(0.01, 0.03, f"{b} rated higher", transform=ax.transAxes, ha="left", va="bottom", color=MUTED, fontsize=7)
    ax.yaxis.grid(True, color=GRID, lw=0.5)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.set_ylabel(f"{a} $-$ {b} rating\n({group}-leaning respondents)")
    fig.tight_layout(pad=0.3)
    save(fig, output)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path,
                        default=ROOT / "data" / "rq4_cis_comparison" / "cis_leader_ratings_by_wave.csv")
    parser.add_argument("--group", default="right")
    parser.add_argument("--leader-a", default="alberto_nunez_feijoo")
    parser.add_argument("--leader-b", default="santiago_abascal")
    parser.add_argument("--output", type=Path, default=None,
                        help="default: paper/figures/cis_<group>_<a>_minus_<b>_by_wave.pdf")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    table = load(args.input, args.group, args.leader_a, args.leader_b)
    output = args.output or ROOT / "paper" / "figures" / (
        f"cis_{args.group}_{args.leader_a}_minus_{args.leader_b}_by_wave.pdf")
    plot(table, args.group, args.leader_a, args.leader_b, output)
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
