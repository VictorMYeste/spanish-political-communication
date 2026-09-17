# Project proposal

Status: draft for discussion (2026-09-17). Items marked **[open]** need a decision.

Working title: *Same message, different speaker? Persona-conditioned moral and cultural reception of Spanish party leaders' political communication*

## 1. Summary

This project extends the design of our EMNLP Findings 2026 paper (*"My own morality, my own mind": Persona-Conditioned Moral and Cultural Reception on Trump Messages*) to the three leaders of the largest Spanish national parties in the XV Legislature: Pedro Sánchez (PSOE), Alberto Núñez Feijóo (PP) and Santiago Abascal (VOX).

Theory-grounded synthetic personas (Inglehart–Welzel cultural values and Moral Foundations Theory) evaluate topic-specific bundles of the leaders' messages with the same structured labels as the previous paper (stance, agreement, legitimacy, offensiveness, endorsement). The study keeps the three-layer evaluation (internal differentiation, explanatory structure, external plausibility) and adds what a single-actor US study could not test: whether reception follows the **message** or the **speaker**, in a multi-party, non-English, highly polarised context, with an external benchmark from the Spanish Centro de Investigaciones Sociológicas (CIS).

The general election is expected in February–March 2027, so results are timely before and during the campaign.

## 2. Relation to the previous paper

Carried over unchanged, unless a decision below says otherwise:

* Persona families and selection strategies (IW-neigh, IW-contour, MFT) with *high*, *mid* and *low* topic-conditioned positions.
* Prompt template, output JSON schema and reception labels.
* Deterministic, auditable corpus construction: quality filters, moral-relevance filter, high-precision topic keyword registry (one topic per message), anonymization as a task-control procedure, prompt-readiness checks.
* RQ1 descriptive analysis, RQ2 closed frequent itemsets with lift, RQ3 topic-level alignment with a survey benchmark.
* No redistribution of raw source text.

Lessons from the previous paper that shape this design:

* **Construct gap.** Pew items measured confidence in the actor's handling of a domain, whereas personas judged concrete messages. Prefer benchmark items that measure agreement with positions or policies when they exist.
* **Neutral mass.** Synthetic outputs put 8–33% of mass on *neutral*, while Pew items had no midpoint. Record the response scale of each benchmark item and compare like with like.
* **Message-driven topics.** *Judiciary/courts* produced uniform rejection with almost no persona separation. In Spain, criticism of judges and courts comes from both the government and the opposition, which lets us test whether that pattern follows the rhetoric or the speaker.
* **Single model.** All generations came from gpt-oss-20b. Add a second model.

## 3. Research questions

* **RQ1, internal differentiation.** Do personas selected for different topic-conditioned positions differ in their reception of the same leader's messages, and do the patterns found for Trump replicate for Spanish leaders?
* **RQ2, explanatory structure.** Which combinations of demographic, cultural and moral persona attributes characterise supportive, rejecting and neutral responses, by actor and topic?
* **RQ3, external plausibility.** Do aggregate synthetic distributions align with CIS public-opinion distributions, both at topic × actor level and for respondent subgroups matched by ideology or vote recall?
* **RQ4, speaker versus message (new).** Within a topic, how much of the reception is explained by the actor, the persona position and their interaction, and how does it change when the speaker is revealed instead of anonymized?
* **RQ5, venue (extension or follow-up paper).** Does reception of the same actor on the same topic differ between X posts, parliamentary interventions and media interviews?

## 4. Actors and sources

| Actor ID | Leader | Party | X account | Congreso | Interviews |
| --- | --- | --- | --- | --- | --- |
| `pedro_sanchez` | Pedro Sánchez | PSOE | @sanchezcastejon | 375 segments | secondary corpus |
| `alberto_nunez_feijoo` | Alberto Núñez Feijóo | PP | @NunezFeijoo | 184 segments | secondary corpus |
| `santiago_abascal` | Santiago Abascal | VOX | @Santi_ABASCAL | 66 segments | secondary corpus |

Congreso counts come from `docs/CONGRESO_RESULTS.md` (2023-09-26 to 2026-06-24). Actor IDs are shared across sources (`config/politicians.json`, `config/x_accounts.json`).

* **X.** Official API v2, pay-per-use, full-archive search. Main corpus for RQ1–RQ4. See `docs/X_SCRIPTS.md`.
* **Congreso.** Official Open Data and *Diario de Sesiones*. See `docs/CONGRESO_SCRIPTS.md`. Main corpus for RQ5; the Abascal subset is small.
* **Interviews.** Given the rights questions around Art. 67 of the Spanish copyright law, treated as a secondary corpus for robustness checks. Process only, never redistribute.

Other leaders (Sumar once it has a leader, Podemos, ERC, Junts, EH Bildu, PNV) can be added through the configuration files. Regional leaders often post in other languages, which would need a separate language decision.

## 5. Time windows

* **Collection window [open]:** from April 2022, when Feijóo became PP leader, to the end of collection. Proposed because it is cheap and gives all three actors in their current leadership roles.
* **Main analysis window:** XV Legislature, from 2023-08-17.
* **Comparison window:** the July 2023 general election campaign, as a reference for campaign versus routine communication.
* **Updates:** weekly incremental collection until the election.

## 6. Corpus construction

1. **Collection.** Raw API responses stored untouched, with query manifests (`docs/X_SCRIPTS.md`).
2. **Normalisation.** Shared internal and stimuli tables per source (`docs/DATA_DICTIONARY.md`).
3. **Quality filters.** Remove retweets (already excluded at query time), decode HTML entities, remove URLs, normalise whitespace, drop very short or low-information posts. **[open]** Keep replies and quote posts, or keep only original posts. Replies and quotes are collected either way and filtered here.
4. **Moral relevance.** ME2-BERT, if it supports Spanish. **[open]** Otherwise a multilingual alternative or translation, validated on a manually annotated sample.
5. **Topic assignment.** Spanish high-precision keyword registry, exactly-one-match rule, `review_needed` for multi-topic hits, as in the previous paper.
6. **Anonymization.** Replace actor names and nicknames, party names and acronyms, campaign slogans and hashtags, and user handles with placeholders (`[POLITICAL_ACTOR]`, `[PARTY]`, `[CAMPAIGN_TAG]`, `[USER]`). Check for identity leaks, degradation and context dependence.
7. **Author role.** Role at the time of posting. **[open]** Roles such as "President of the Government" identify the actor. Decide whether the anonymized condition uses a neutral role (e.g., "a Spanish national party leader") and the attributed condition of RQ4 reveals it.
8. **Bundles.** Topic-specific, single-actor, date-ordered bundles, balanced by actor within each topic and period.

## 7. Candidate topics

The final set is the overlap between topics with enough messages from all three actors and topics with CIS items, as with Pew in the previous paper.

| Candidate topic | Examples of expected content |
| --- | --- |
| `immigration_border` | arrivals, regularisation, unaccompanied minors, border control |
| `housing_cost_of_living` | housing, rents, prices, wages |
| `economy_employment` | employment, taxes, pensions, growth |
| `judiciary_rule_of_law` | judges, courts, Constitutional Court, Attorney General, "lawfare" |
| `corruption` | corruption investigations and accountability |
| `territorial_model` | Catalonia, amnesty, regional financing |
| `foreign_policy_defence` | Ukraine, Gaza, NATO, defence spending, trade and tariffs |
| `gender_equality` | feminism, gender violence, equality policies |
| `emergencies_climate` | floods, wildfires, energy, climate policy |

## 8. Personas **[open, Candida]**

* **Option A.** Reuse the WVS-grounded persona pool of Greco et al. (2026) unchanged, keeping the previous paper's selection procedure for comparability.
* **Option B.** Build a Spain-grounded pool from EVS/WVS data for Spain, possibly adding Spain-relevant variables such as left–right self-placement and territorial identity. This makes subgroup matching with CIS respondents easier (RQ3).

Topic-to-dimension mappings (IW axes, MFT foundations) must be fixed before generation, as in the previous paper, including rationales for the new Spanish topics.

## 9. External benchmark: CIS

CIS barómetros are published monthly with free microdata, and the CIS runs a pre-electoral survey before general elections. Items to inventory, with availability checked wave by wave:

* Ratings of and confidence in party leaders.
* Most important problems facing Spain (issue salience by topic).
* Opinion on specific policies when asked (e.g., amnesty, immigration, housing measures). These are preferred because they reduce the construct gap.
* Ideological self-placement (1–10), vote recall and vote intention, used to build subgroup benchmarks.

Selection follows the Pew protocol: deterministic inventory, inclusion and exclusion codes, weighted distributions, positive/negative/neutral/missing mapping, rule trace per item.

## 10. Generation setup

* gpt-oss-20b served with vLLM through an OpenAI-compatible API, temperature 0.01, max 4096 tokens, same hardware as the previous paper.
* **[open]** Second model for cross-model replication, preferably open weights with strong Spanish (e.g., a BSC ALIA/Salamandra model or another multilingual open model).
* **[open]** Prompt language: Spanish messages with the English prompt template, or a validated Spanish translation of the template.

## 11. Analyses

* **RQ1.** Mean stance and label distributions by strategy × topic × position × actor, at bundle and message level; best versus top-5.
* **RQ2.** Expanded persona samples and closed frequent itemsets with lift, per strategy × topic × actor.
* **RQ3.** Topic × actor net scores against CIS; Pearson and Spearman correlations, MAE, top-topic recovery; subgroup-matched comparison if personas allow it.
* **RQ4.** Models of stance and labels with actor, persona position, their interaction and anonymized/attributed condition as predictors, with persona and bundle as random effects (e.g., ordinal mixed-effects models).
* **RQ5.** Same design with bundles drawn from each venue, restricted to actor × topic cells with enough material.

## 12. Ethics, legal and data release

* X data is collected only through the official API under the developer terms. No scraping or account pools.
* Politicians' public statements are still personal data under the GDPR. Processing is limited to research purposes, stored securely, and not used to profile private individuals.
* Released material: code, configuration, rule tables, audit artifacts, post IDs and derived labels. Not released: post text, Congreso caches, interview transcripts or audio.
* A DSA Art. 40(12) data access application is submitted in parallel, but the project does not depend on it.
* Persona labels are selection positions, not claims about real voters or party electorates.

## 13. Timeline (proposal)

| Period | Milestone |
| --- | --- |
| Sep–Oct 2026 | X collection; CIS item inventory; persona decision; topic registry draft |
| Oct–Nov 2026 | Moral relevance, topics, anonymization, prompt-ready pool; RQ1 and RQ4 generations |
| Nov–Dec 2026 | RQ2 and RQ3 analyses; Congreso bundles for RQ5; paper draft |
| Jan 2027 | Preprint before the campaign; submission to the target venue **[open]** |

## 14. Open decisions

1. Collection window start (April 2022 proposed).
2. Replies and quote posts: keep or drop.
3. Personas: option A or B.
4. Neutral versus revealing author roles in the anonymized condition.
5. Second model and prompt language.
6. Whether RQ5 belongs in this paper or a follow-up.
7. Target venue and deadline.
