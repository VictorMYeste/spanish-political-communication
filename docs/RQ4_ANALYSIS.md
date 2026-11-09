# RQ4 analysis: comparison with CIS

> Do the relative preferences for Sánchez, Feijóo and Abascal among left-, centre- and right-leaning synthetic personas resemble the relative leader evaluations found among the corresponding ideological groups in CIS data? ([PROPOSAL.md](PROPOSAL.md), section 3)

Code: `src/collect_cis_microdata.py` downloads the CIS microdata, `src/analyze_cis_comparison.py` compares them with the personas. The persona side is the output of the RQ2 analysis ([RQ2_ANALYSIS.md](RQ2_ANALYSIS.md)), so that must be run first.

## CIS data

* **Waves.** Every monthly barometer from April 2022 to October 2026, the window of the posts and of the topic selection ([TOPIC_SELECTION.md](TOPIC_SELECTION.md)): 48 barometers. The 7 other months (August of each year, and June and September 2023, around the general election) have no barometer.
* **Item.** The rating of each leader: *¿qué valoración le merece cada uno/a de ellos/as? Puntúelos/as de 1 a 10, sabiendo que el 1 significa que lo/a valora 'muy mal' y el 10 que lo/a valora 'muy bien'* (`VALORALIDERES`). It is the only leader item that covers all three leaders in every wave; the confidence items leave out Abascal.
* **Groups.** The self-placement question (`ESCIDEOL`), the same CIS item the personas answered ([PERSONA_IDEOLOGY.md](PERSONA_IDEOLOGY.md)), with the same cut-offs: left 1–4, centre 5–6, right 7–10.
* **Respondents.** Those who place themselves from 1 to 10 and rate all three leaders, so the means of the leaders and the differences between them come from the same people, as the personas' complete quartets do. Left out: "does not know the leader" (0), N.S. (98) and N.C. (99).
* **Weights.** Each wave's national weight (`PESO`; `PESO_V2` in October 2025). The CIS added an adjustment for education in February 2024.

Columns are found by their labels, not their positions: in May 2023 Abascal's rating is the third item, not the fourth.

## What it estimates

* **Per wave.** The weighted mean rating of each leader, and the weighted mean of the difference between each pair of leaders, by group and by placement 1–10. Standard errors are linearised for a weighted mean.
* **Pooled.** The mean over waves, with every wave weighing the same, as in the topic selection. Waves are independent samples, so the pooled variance is the sum of the wave variances divided by the number of waves squared. The intervals ignore the clustering of the sample design and are therefore somewhat narrow. This does not affect the comparison, which uses only the order and sign of the estimates.
* **Congruence.** The left-minus-right difference of each pair of leaders, as in RQ2.

## Comparison

Only order and direction are compared, not size: a CIS rating and a persona attribution effect are on different scales and measure different things (a general evaluation of the leader versus the change in how a message is received when the leader's name is on it).

* **Order.** Within each group and each placement, the order of the three leaders by CIS mean rating and by persona attribution effect (RQ2, both frameworks, all topics, stance by default).
* **Direction.** Within each group, the sign of each of the three differences between leaders, with both confidence intervals; and the sign of the three left-minus-right contrasts.
* **Over time.** Preferences changed during the window, so the outputs also give each wave's estimates and, for each group and placement, the number of waves whose order matches the personas'.

The `all` row is shown for reference but is not a like-for-like comparison: the groups' shares differ between CIS respondents and personas.

## Running it

```bash
python src/collect_cis_microdata.py      # about 2 minutes; files already downloaded are reused
python src/analyze_cis_comparison.py     # about 5 seconds
```

`collect_cis_microdata.py` reads each study page on cis.es, takes the link to the data file ("Fichero datos") from the page's structured data, and keeps the zip and its SPSS file in `data/raw/cis_microdata/` with a `manifest.csv`. `analyze_cis_comparison.py` takes `--model`, `--frame` and `--outcome` to compare with other RQ2 outputs.

| Output, in `data/rq4_cis_comparison/` | Content |
| --- | --- |
| `cis_leader_ratings_by_wave.csv` | Each wave's mean rating of each leader and difference between leaders, by group and placement, with SE and N |
| `cis_leader_ratings_pooled.csv` | The same pooled over the waves, with CI |
| `cis_congruence_pooled.csv` | Left-minus-right difference of each pair of leaders, with CI |
| `<model>/rq4_orderings_<frame>.csv` | Order of the leaders by group and placement, CIS and personas, and the number of waves with the personas' order |
| `<model>/rq4_pairs_<frame>.csv` | Differences between leaders within each group and left minus right, CIS and personas, with CIs and whether the signs agree |
| `<model>/rq4_summary_<frame>.json` | Waves, respondents left out, non-default columns, agreement counts |

## Figures

```bash
python src/plot_by_placement.py     # (a) persona effects and (b) CIS ratings by placement 1-10
python src/plot_cis_over_time.py    # right-leaning respondents' Feijóo-minus-Abascal rating, wave by wave
```

Both write a PDF and a PNG to `paper/figures/`. `plot_cis_over_time.py` takes `--group`, `--leader-a` and `--leader-b` to draw another difference.

## Possible extra for December

Ask the personas the CIS leader-rating question itself, in a separate call as for the self-placement (791 calls). That would compare a rating with a rating, and separate the persona's view of a leader from the effect of the leader's name on how a message is received. It needs one more run on the cluster.
