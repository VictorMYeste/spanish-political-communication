# Persona ideology

The elicited ideological self-placement of [PROPOSAL.md](PROPOSAL.md), section 8, used to form the left, centre and right groups of RQ2 and RQ4. Code: `src/elicit_persona_ideology.py`.

Every persona of the reaction runs answers the CIS left–right question once, in a call of its own with no post in it. The answer is the model's reading of the persona, not ground truth, and it is never shown to the persona when it judges posts. Since each call starts with no memory, it does not matter that the question is asked after the reactions.

## The question

The CIS barometers ask, with this wording in every wave since October 2023:

> En general, cuando se habla de política se utilizan normalmente las expresiones izquierda y derecha. Situándonos en una escala que va del 1 al 10, en la que 1 significa 'lo más a la izquierda' y 10 'lo más a la derecha', ¿dónde se colocaría Ud.?

From April 2022 to July 2023 it lacked "En general" and ended "¿en qué casilla se colocaría Ud.?". The scale and its anchors are the same.

The personas get it in English, the language of the reaction prompts, with the scale anchored to Spanish politics:

> In general, when talking about politics in Spain, the expressions "left" and "right" are normally used. On a scale from 1 to 10, where 1 means "the furthest to the left" and 10 means "the furthest to the right" of Spanish politics, where would you place yourself?

The system prompt and the persona profile are those of the reactions (*Role-play the person described in the supplied persona profile…*), followed by the question and the schema `{"left_right_self_placement": "integer from 1 to 10"}`.

Choices:

* **Scale anchored to Spain.** Most personas are not Spanish (the United States, Poland and Sweden lead; 27 of the 1,200 persona slots are in Spain). Without an anchor each one would answer on its own country's scale, and the groups could not be compared with the leaders (RQ2) or with CIS respondents (RQ4).
* **Personas are not made residents of Spain.** The reaction prompts did not say so, and the answer must come from the same persona that judged the posts.
* **No parties or leaders in the question.** Leader preferences are the outcome of RQ4, so they must not form the groups. The model's reading of the Spanish scale is already covered by RQ0, where it places Sánchez at 3, Feijóo at 8 and Abascal at 9.
* **No "don't know" option.** The CIS item offers only 1–10; "N.S." and "N.C." are recorded when given (3.4% in October 2026). A persona whose answer is not an integer from 1 to 10 after the retries is left out of the groups.
* **Same settings as the reactions:** gpt-oss-20b, temperature 0, seed 42, JSON mode, 4,096 tokens.

The groups are left 1–4, centre 5–6 and right 7–10 (`GROUPS` in the script). The RQ2 analysis ([RQ2_ANALYSIS.md](RQ2_ANALYSIS.md)) uses them for the personas, and the RQ4 analysis ([RQ4_ANALYSIS.md](RQ4_ANALYSIS.md)) applies them to CIS respondents. The script prints the split at the end, to check that no group is too small (the personas cannot be extended without running the reactions again).

## Running it

The persona text is built by the reaction script that produced the reactions, so the answer comes from exactly the same profile. It is not in the repository; pass it with `--reactions-script` (by default `src/` or `candida_spanish_reactions/generate_spanish_counterfactual_reactions_v1_3_0.py`). A persona selected for several topics has the same text in all of them and is asked once: 441 IW and 350 MFT personas, 791 calls.

```bash
python src/elicit_persona_ideology.py --framework iw  --reactions-dir data/spanish_reactions --dry-run
python src/elicit_persona_ideology.py --framework iw  --reactions-dir data/spanish_reactions --api-base http://127.0.0.1:8000/v1
python src/elicit_persona_ideology.py --framework mft --reactions-dir data/spanish_reactions --api-base http://127.0.0.1:8000/v1
```

`--reactions-dir` compares each persona's `persona_sha256` with the reaction outputs and stops if any differs. `--allow-mismatch` asks them anyway and marks them `matches_reactions=False`. The run resumes: personas already answered are skipped.

| Output | Content |
| --- | --- |
| `data/persona_ideology/<model>/<framework>_<frame>.jsonl` | One record per call, with the raw model output |
| `data/persona_ideology/<model>/<framework>_<frame>.csv` | One row per persona: `persona_id`, `persona_sha256`, `matches_reactions`, `topics`, `left_right_self_placement`, `status`, `error` |

Join to the reactions on `framework` and `persona_id`.

## Values and self-placement

The personas were selected by their IW or MFT profile for each topic (high, mid and low levels, 50 personas each), and no prompt mentions ideology. The left, centre and right groups therefore come from the values in the persona profiles. `src/describe_persona_values.py` shows how, with no model calls:

```bash
python src/describe_persona_values.py    # about 1 second
```

| Output, in `data/persona_values/<model>/` | Content |
| --- | --- |
| `profile_groups_<frame>.csv` | For each framework, topic and profile level: personas in each group, their shares, and the mean placement. A persona selected for several topics counts in each |
| `value_correlations_<frame>.csv` | Spearman correlation between the placement and each value dimension in the framework's prompts (IW: the two coordinates; MFT: the six MFQ-2 foundations), one row per persona, with a 95% CI |
| `summary_<frame>.json` | Persona counts, slots without a placement, and cells that hold exactly the same personas |

Results for gpt-oss-20b, Spain frame:

* **Topic profiles map onto left and right** wherever the profile has a political direction. For immigration, 82% (IW) and 88% (MFT) of the inclusive profile are left-leaning, and 64% and 100% of the restrictive profile right-leaning. For MFT, the redistribution and merit profiles of the economy, and the basic-needs and market profiles of household costs, are entirely left or entirely right. Corruption profiles do not separate by ideology in either framework.
* **The placement follows the values shown to the model.** MFT: Care −0.87, Equality −0.67, Proportionality 0.67, Authority 0.59, Loyalty 0.43, Purity 0.21. IW: survival/self-expression −0.60, but traditional/secular-rational only −0.06.
* **Some MFT cells are not independent.** The selection targets of economy-high and household-low are identical, as are those of economy-low and household-high, and all four mid levels share one target. Each of these sets holds the same 50 personas. The fully one-sided MFT cells partly show that the extremes of Care and Equality versus Proportionality are read as left versus right.

## Possible extras for December

Not in the preprint. Each one reuses the script.

* **Stability.** Ask twice more with reworded questions and report how often the answers agree within one point. Reviewers may question elicited ideology, and this costs about 1,600 more calls. The rewordings still have to be written; add them to `QUESTIONS` in the script.
* **Frame of reference.** Asking the same question without "Spain" would show how much the frame of reference matters. That's about 800 more calls, and reviewers may ask about it. Already in the script: `--frame generic`.
