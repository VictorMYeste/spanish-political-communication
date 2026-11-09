"""Plot the leader preferences by left-right placement: personas (RQ2) and CIS respondents (RQ4).

Panel (a) reads rq2_by_placement_<frame>.csv (analyze_ideological_congruence.py):
the attributed-minus-anonymous effect of each leader's name, with its 95% CI, at
each elicited placement. Placements with fewer than --min-personas personas are
not drawn (placement 1 has a single persona, so its interval cannot be
estimated). Panel (b), unless --no-cis, reads cis_leader_ratings_pooled.csv
(analyze_cis_comparison.py): the mean 1-10 rating of each leader by CIS
respondents at each self-placement, pooled over the barometers.
"""

import argparse
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from analyze_ideological_congruence import LEADERS, safe_slug  # noqa: E402
from elicit_persona_ideology import GROUPS  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
# Party colours (PSOE red, PP blue, Vox green). Red and green are close for
# protanopes, so every leader also has its own marker and a direct label.
STYLE = {
    "pedro_sanchez": {"label": "Sánchez", "color": "#e34948", "marker": "o"},
    "alberto_nunez_feijoo": {"label": "Feijóo", "color": "#2a78d6", "marker": "s"},
    "santiago_abascal": {"label": "Abascal", "color": "#008300", "marker": "^"},
}
INK, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7"
DODGE = 0.16
PLACEMENTS = list(range(1, 11))
PANEL_SIZE = (3.05, 2.5)
YLABELS = {
    "stance": "Effect on stance (named $-$ anonymous)",
    "offensiveness": "Effect on offensiveness (named $-$ anonymous)",
}


def setup_style():
    plt.rcParams.update({
        "font.family": "sans-serif", "font.size": 8, "pdf.fonttype": 42,
        "axes.edgecolor": AXIS, "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
    })


def load_personas(path, framework, outcome, min_personas):
    table = pd.read_csv(path)
    table = table[(table.framework == framework) & (table.outcome == outcome)]
    if table.empty:
        raise ValueError(f"No rows for framework={framework}, outcome={outcome} in {path}")
    counts = table.groupby("placement").n_personas.first()
    dropped = sorted(counts[counts < min_personas].index)
    return table[~table.placement.isin(dropped)], counts, dropped


def load_cis(path):
    table = pd.read_csv(path)
    table = table[(table.level == "placement") & table["item"].isin(LEADERS)].copy()
    if table.empty:
        raise ValueError(f"No placement rows in {path}")
    table["placement"] = table.unit.astype(int)
    table["leader"] = table["item"]
    return table, table.groupby("placement").n.first()


def draw_panel(ax, table, count_labels, ylabel, label_gap, zero_line):
    """One line per leader over placements 1-10, with CIs, direct labels and the group cut-offs."""
    ends = []
    for i, leader in enumerate(LEADERS):
        style = STYLE[leader]
        rows = table[table.leader == leader].set_index("placement").sort_index()
        x = [p + (i - 1) * DODGE for p in rows.index]
        ax.plot(x, rows.estimate, color=style["color"], lw=1.4, zorder=3)
        ax.errorbar(x, rows.estimate, yerr=[rows.estimate - rows.ci_low, rows.ci_high - rows.estimate],
                    fmt="none", ecolor=style["color"], elinewidth=0.9, capsize=0, zorder=3)
        ax.plot(x, rows.estimate, linestyle="none", marker=style["marker"], markersize=4.2,
                markerfacecolor=style["color"], markeredgecolor="white", markeredgewidth=0.6,
                label=style["label"], zorder=4)
        ends.append([rows.estimate.iloc[-1], style["label"]])

    # Direct labels at the line ends, nudged apart so that they do not overlap.
    ends.sort()
    for k in range(1, len(ends)):
        ends[k][0] = max(ends[k][0], ends[k - 1][0] + label_gap)
    for y, label in ends:
        ax.text(PLACEMENTS[-1] + 0.45, y, label, va="center", ha="left", color=INK, fontsize=7.5)

    if zero_line:
        ax.axhline(0, color=AXIS, lw=0.8, zorder=1)
    ax.yaxis.grid(True, color=GRID, lw=0.5)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)

    # Group cut-offs used in the tables (left 1-4, centre 5-6, right 7-10).
    for values in list(GROUPS.values())[1:]:
        ax.axvline(values.start - 0.5, color=AXIS, lw=0.7, ls=(0, (2, 2)), zorder=1)
    for name, values in GROUPS.items():
        ax.text((values.start + values.stop - 1) / 2, 1.01, name.capitalize(), transform=ax.get_xaxis_transform(),
                ha="center", va="bottom", color=MUTED, fontsize=7.5)

    ax.set_xticks(PLACEMENTS)
    ax.set_xticklabels([f"{p}\n{count_labels.get(p, '')}" for p in PLACEMENTS], fontsize=7)
    ax.text(0.25, -0.075, "n", transform=ax.get_xaxis_transform(), ha="right", va="top",
            color=MUTED, fontsize=7, style="italic")
    ax.set_xlim(0.5, PLACEMENTS[-1] + 1.45)
    ax.set_xlabel("Left–right self-placement")
    ax.set_ylabel(ylabel)


def thousands(n):
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def save(fig, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    if output.suffix != ".png":
        fig.savefig(output.with_suffix(".png"), dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot(personas, persona_counts, outcome, output, cis=None, cis_counts=None):
    setup_style()
    panels = 1 if cis is None else 2
    fig, axes = plt.subplots(1, panels, figsize=(PANEL_SIZE[0] * panels + 0.15 * (panels - 1), PANEL_SIZE[1]),
                             squeeze=False)
    axes = axes[0]
    draw_panel(axes[0], personas, {p: str(n) for p, n in persona_counts.items()},
               YLABELS.get(outcome, f"Effect on {outcome}"), label_gap=0.045, zero_line=True)
    if cis is not None:
        draw_panel(axes[1], cis, {p: thousands(n) for p, n in cis_counts.items()},
                   "Mean rating of the leader (1–10)", label_gap=0.4, zero_line=False)
        axes[1].set_ylim(1, 7.5)
        for ax, title in zip(axes, ["(a) Personas: effect of the leader's name", "(b) CIS respondents: leader rating"]):
            ax.set_title(title, fontsize=8, color=INK, loc="left", pad=14)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=3, frameon=False,
               fontsize=7.5, handlelength=0.8, handletextpad=0.4, columnspacing=1.2, borderaxespad=0)
    fig.tight_layout(pad=0.3, w_pad=1.2)
    save(fig, output)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    parser.add_argument("--frame", default="spain")
    parser.add_argument("--input-dir", type=Path, default=ROOT / "data" / "rq2_ideological_congruence")
    parser.add_argument("--cis-ratings", type=Path,
                        default=ROOT / "data" / "rq4_cis_comparison" / "cis_leader_ratings_pooled.csv")
    parser.add_argument("--no-cis", action="store_true", help="draw only the persona panel")
    parser.add_argument("--framework", default="all", choices=["all", "iw", "mft"])
    parser.add_argument("--outcome", default="stance")
    parser.add_argument("--min-personas", type=int, default=2)
    parser.add_argument("--output", type=Path, default=None,
                        help="default: paper/figures/placement_personas_cis.pdf, or rq2_by_placement_<outcome>.pdf with --no-cis")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    path = args.input_dir / safe_slug(args.model) / f"rq2_by_placement_{args.frame}.csv"
    personas, persona_counts, dropped = load_personas(path, args.framework, args.outcome, args.min_personas)
    cis, cis_counts = (None, None) if args.no_cis else load_cis(args.cis_ratings)
    name = f"rq2_by_placement_{args.outcome}.pdf" if args.no_cis else "placement_personas_cis.pdf"
    output = args.output or ROOT / "paper" / "figures" / name
    plot(personas, persona_counts, args.outcome, output, cis, cis_counts)
    print(f"Wrote {output}")
    if dropped:
        print(f"Persona placements not drawn (fewer than {args.min_personas} personas): {dropped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
