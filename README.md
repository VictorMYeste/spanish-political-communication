# Spanish Political Communication

Persona-conditioned moral and cultural reception of political messages by the leaders of the main Spanish national parties.

Status: study design agreed on 7 October 2026; corpus preparation for the first paper.

## Overview

This project extends our previous study on Trump's posts (Findings of EMNLP 2026; [code](https://github.com/VictorMYeste/trump-moral-audiences-pipeline)) to three Spanish party leaders in the XV Legislature:

* Pedro Sánchez (PSOE)
* Alberto Núñez Feijóo (PP)
* Santiago Abascal (VOX)

The first paper asks whether the speaker's name changes how the same message is judged. Synthetic personas from a culturally grounded persona pool, with Inglehart–Welzel cultural values and Moral Foundations profiles, judge each X post in four conditions: without an author, and attributed to Sánchez, Feijóo or Abascal. The message stays the same; only the name changes. Personas return the same structured judgments as in the previous study: stance, agreement, legitimacy, offensiveness and endorsement.

Before seeing any post, each persona answers the 1–10 left–right self-placement question of the Centro de Investigaciones Sociológicas (CIS). This elicited ideological self-placement is the model's interpretation of the persona, not ground truth. It is used only to form left, centre and right groups and is never shown to the persona when it judges messages. The relative preferences for the three leaders in each group are then compared with those of the corresponding ideological groups in CIS surveys.

The first paper uses X posts only. Parliamentary interventions and interviews are left for a follow-up paper. The general election is expected in early 2027.

The full study design and open decisions are in [docs/PROPOSAL.md](docs/PROPOSAL.md).

## Research questions

Core of the first paper:

* **RQ1, speaker attribution.** How does attributing the same message to Sánchez, Feijóo or Abascal, rather than presenting it anonymously, change its evaluation by synthetic personas?
* **RQ2, ideological congruence.** Does the speaker-attribution effect depend on the persona's elicited ideological self-placement? Do personas respond more positively when the attributed speaker is ideologically closer to them?
* **RQ3, topics.** Does the size or direction of the speaker-attribution effect vary across salient political topics, such as immigration, housing and the cost of living?
* **RQ4, comparison with CIS.** Do the relative preferences for the three leaders among left-, centre- and right-leaning personas resemble those of the corresponding ideological groups in CIS data? The comparison concerns the ordering and direction of the preferences, not the scores.

If time allows, otherwise in a follow-up paper:

* **RQ5, electoral context.** Do the leaders' communication and its reception change between normal, pre-election, campaign and post-election periods? Is the effect of the speaker's name stronger during campaigns?

Held in reserve, in case reviewers ask for it:

* **RQ6, robustness across models.** Are the speaker-attribution, ideological-congruence and topic effects consistent across different LLMs?

## Sources

| Source | Paper | Status | Documentation |
| --- | --- | --- | --- |
| X posts | First | Collected: 10,863 posts, 2022-04 to 2026-10 | [docs/X_SCRIPTS.md](docs/X_SCRIPTS.md), [docs/X_RESULTS.md](docs/X_RESULTS.md) |
| Congreso de los Diputados interventions | Follow-up | Collection and text extraction pipeline available | [docs/CONGRESO_SCRIPTS.md](docs/CONGRESO_SCRIPTS.md), [docs/CONGRESO_RESULTS.md](docs/CONGRESO_RESULTS.md) |
| La Moncloa transcripts (Sánchez) | Follow-up | Collection and text extraction pipeline available | [docs/MONCLOA_SCRIPTS.md](docs/MONCLOA_SCRIPTS.md) |
| PP media and official documents (Feijóo) | Follow-up | Media inventory and PDF text extraction pipeline available | [docs/PP_MEDIA_SCRIPTS.md](docs/PP_MEDIA_SCRIPTS.md) |

All sources share the actor identifiers in `config/politicians.json` and the table formats in [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md).

## Repository layout

```
config/   politicians.json      actor registry shared by all sources
          x_accounts.json       X handles and user IDs, joined by actor_id
          x_collection.json     X collection window, query and fields
          moncloa_source.json   La Moncloa archive (follow-up paper)
          pp_media_source.json  PP media and documents (follow-up paper)
src/      check_x_access.py, collect_x.py, build_x_tables.py
          collect_congreso.py, extract_congreso_text.py
          collect_moncloa.py, extract_moncloa_text.py
          collect_pp_media.py, extract_pp_documents.py
tests/    pytest tests, no network access
docs/     proposal, data dictionary and per-source documentation
data/     generated locally and never committed (raw/, processed/, cache/)
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest
```

X credentials are only needed for the X scripts. Copy `.env.example` to `.env` and fill in `X_BEARER_TOKEN`; `.env` is ignored by git.

## X

Posts written by the leaders are collected with full-archive search, excluding retweets. The collector counts the matching posts and prints the cost before downloading anything.

```bash
python src/check_x_access.py --write-ids   # check access, resolve account IDs
python src/collect_x.py --counts-only      # how many posts and what they would cost
python src/collect_x.py                    # counts, confirms, then downloads
python src/build_x_tables.py               # internal and stimuli tables
python src/assign_x_topics.py              # RQ3 topics (docs/TOPIC_SELECTION.md)
python src/prepare_x_stimuli.py            # quality filters, anonymization, role markers (docs/STIMULI.md)
python src/sample_x_stimuli.py            # stimulus selection, keep_for_prompt (docs/STIMULI.md)
```

Costs, options and data handling are described in [docs/X_SCRIPTS.md](docs/X_SCRIPTS.md).

## Persona ideology

Each persona of the reaction runs answers the CIS left–right self-placement question, used to form the ideological groups of RQ2 and RQ4. The wording, choices and outputs are in [docs/PERSONA_IDEOLOGY.md](docs/PERSONA_IDEOLOGY.md).

```bash
python src/elicit_persona_ideology.py --framework iw --reactions-dir data/spanish_reactions --dry-run
```

## RQ2 analysis

The speaker-attribution effect by left, centre and right persona group, and the congruence test. Method and outputs in [docs/RQ2_ANALYSIS.md](docs/RQ2_ANALYSIS.md).

```bash
python src/analyze_ideological_congruence.py
```

## RQ4 analysis

The order of the leaders' preferences by ideological group, personas against CIS respondents (leader ratings from the 48 barometers of the window). Method and outputs in [docs/RQ4_ANALYSIS.md](docs/RQ4_ANALYSIS.md).

```bash
python src/collect_cis_microdata.py
python src/analyze_cis_comparison.py
```

## Sources for the follow-up paper

The Congreso, La Moncloa and PP pipelines stay in the repository for the follow-up paper. Their commands, outputs and quality checks are in [docs/CONGRESO_SCRIPTS.md](docs/CONGRESO_SCRIPTS.md), [docs/MONCLOA_SCRIPTS.md](docs/MONCLOA_SCRIPTS.md) and [docs/PP_MEDIA_SCRIPTS.md](docs/PP_MEDIA_SCRIPTS.md).

Recheck the official Congreso source and reuse conditions at collection time:

* [Congreso Open Data — interventions](https://www.congreso.es/es/opendata/intervenciones)
* [Congreso legal and reuse notice](https://www.congreso.es/es/cem/aviso-legal)

## Data handling and ethics

* The repository contains code, configuration and documentation only. Collected data, caches and credentials are never committed.
* Each source produces an internal table with full provenance and a stimuli table for the persona experiments. Stimuli are pseudonymised, not anonymous, because the original text remains searchable.
* X data is collected only through the official API. Only post IDs and derived labels may be shared.
* Congreso and La Moncloa material is used under the reuse conditions of each source. Interview material is processed for research and not redistributed.
* Politicians' public statements are still personal data under the GDPR. They are processed for academic, non-commercial research only.
* The attributed conditions show posts under the names of leaders who did not write them. These counterfactual attributions exist only inside the prompts and are never published as statements by those leaders.
* Elicited ideological self-placement is the model's interpretation of a persona, not ground truth. Persona groups are not claims about real voters or party electorates.

## Contributing

* Create one branch per task from an up-to-date `main`. When it is ready, merge it into `main` with a merge commit (`git merge --no-ff`) and push `main`. Pull requests and reviews are not required.
* Keep changes to shared files (`README.md`, `.gitignore`, `requirements.txt`, `config/politicians.json`, `docs/PROPOSAL.md`, `docs/DATA_DICTIONARY.md`) small and in separate commits.
* Run `python -m pytest` before pushing to `main`.

## Citation

A citation for this project will be added when a preprint is available. Previous study:

> Candida Maria Greco, Víctor Yeste, Andrea Tagarelli, and Paolo Rosso. 2026. "My own morality, my own mind": Persona-Conditioned Moral and Cultural Reception on Trump Messages. In *Findings of the Association for Computational Linguistics: EMNLP 2026*.

## License

The code and documentation in this repository are released under the [Apache License 2.0](LICENSE). Source data are not included and remain subject to their own terms.
