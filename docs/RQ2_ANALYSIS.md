# RQ2 analysis: ideological congruence

> Does the speaker-attribution effect depend on the persona's elicited ideological self-placement? In particular, do personas respond more positively when the attributed speaker is ideologically closer to them? ([PROPOSAL.md](PROPOSAL.md), section 3)

Code: `src/analyze_ideological_congruence.py`. Inputs: the reactions (`data/spanish_reactions`), the elicited placements ([PERSONA_IDEOLOGY.md](PERSONA_IDEOLOGY.md)) and `keep_for_prompt` from `data/processed/x_stimuli.csv`.

## What it estimates

The RQ1 effect, split by persona group:

* **Effect.** For each persona and post, the response with a leader's name minus the response to the anonymous version. Stance is coded −1 (reject), 0 (neutral), +1 (support); agreement, legitimacy, offensiveness and endorsement keep their 1–5 scale. Only pairs observed in all four conditions are used, as in RQ1.
* **Posts.** The 8 run posts that failed the stimulus checks (`keep_for_prompt` false) are dropped, leaving the 1,075 stimuli.
* **Groups.** Left 1–4, centre 5–6, right 7–10 on the elicited CIS scale (`GROUPS` in `src/elicit_persona_ideology.py`), the same cut-offs that RQ4 applies to CIS respondents. A persona is grouped only if its placement was asked with the same persona text that judged the posts (`persona_sha256`).
* **Confidence intervals.** 95%, two-way cluster-robust by message and persona (Cameron, Gelbach and Miller, 2011), as in RQ1. With every post kept, the script reproduces the RQ1 intervals exactly.

## Congruence

The leaders are ordered left to right as the persona model places them in RQ0: Sánchez (3), Feijóo (8), Abascal (9). For each pair of leaders A and B, with A to the left of B:

* **Within a group:** effect of A minus effect of B. Positive means the group penalises A less than B.
* **Left minus right:** (A minus B among left personas) minus (A minus B among right personas). This is the congruence test. It is positive under congruence for stance, agreement, legitimacy and endorsement, and negative for offensiveness, where higher means more offensive.

Every estimate is given for both frameworks together and for IW and MFT separately, over all topics and for each topic (RQ3 × RQ2). The effect by placement 1–10, over all topics, is there for a figure.

## Running it

```bash
python src/analyze_ideological_congruence.py
```

It takes about 20 seconds. `--model` selects another persona model's reactions and placements (December), and `--frame generic` the placements asked without the Spanish anchor.

| Output, in `data/rq2_ideological_congruence/<model>/` | Content |
| --- | --- |
| `rq2_group_effects_<frame>.csv` | Effect by group (and `all`, the RQ1 estimate) × leader, with CI and the number of pairs, personas and posts |
| `rq2_contrasts_<frame>.csv` | `within_group` and `left_minus_right` contrasts, with CI |
| `rq2_by_placement_<frame>.csv` | Effect by placement 1–10 × leader |
| `rq2_summary_<frame>.json` | Settings, dropped posts, pair counts, personas per group, personas left out |
