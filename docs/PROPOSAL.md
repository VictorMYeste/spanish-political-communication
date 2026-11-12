# Project proposal

Status: study design agreed at the meeting of 2026-10-07 (Víctor, Candida, Paolo). Items marked **[open]** need a decision.

Working title: *Same message, different speaker? Persona-conditioned moral and cultural reception of Spanish party leaders' political communication*

## 1. Summary

The first paper of this project is a speaker-attribution experiment on X posts by the leaders of the three largest Spanish national parties in the XV Legislature: Pedro Sánchez (PSOE), Alberto Núñez Feijóo (PP) and Santiago Abascal (VOX).

Synthetic personas judge the same post in four conditions:

* without an author;
* attributed to Sánchez;
* attributed to Feijóo;
* attributed to Abascal.

The message stays the same; only the name changes. This tests whether the speaker's name changes how the same message is judged, which our single-actor EMNLP Findings 2026 paper on Trump (*"My own morality, my own mind": Persona-Conditioned Moral and Cultural Reception on Trump Messages*) could not test. It is the main new contribution.

The effect is then broken down by the personas' elicited ideological self-placement (left, centre, right) and by topic, and compared with the relative evaluations of the three leaders by the corresponding ideological groups in surveys of the Spanish Centro de Investigaciones Sociológicas (CIS).

X posts are the only corpus of this paper. Parliamentary interventions and interviews are left for a follow-up paper (§4). The general election is scheduled for 29 November 2026.

## 2. Relation to the previous paper

Carried over, unless a decision below says otherwise:

* The culturally grounded persona pool of Greco et al. (2026), with Inglehart–Welzel (IW) coordinates and MFQ-2 moral-foundation profiles.
* Prompt template, output JSON schema and reception labels (stance, agreement, legitimacy, offensiveness, endorsement).
* Deterministic, auditable corpus construction: quality filters, moral-relevance filter, high-precision topic keyword registry (one topic per message), anonymization as a task-control procedure, prompt-readiness checks.
* No redistribution of raw source text.

Changed:

* The central question is the speaker, not the differentiation between personas. The previous RQ1 (differences across topic-conditioned persona positions) and RQ2 (closed frequent itemsets) are not research questions of this paper.
* Personas are compared by their elicited ideological self-placement (§8).
* The external benchmark compares the ordering of leader preferences by ideological group, not topic-level distributions, because the CIS has no topic-by-leader items comparable to the Pew items of the previous paper (§9).

Lessons from the previous paper that shape this design:

* **Construct gap.** Pew items measured confidence in the actor's handling of a domain, whereas personas judged concrete messages. The CIS comparison is therefore limited to the ordering and direction of leader preferences, not their scores.
* **Neutral mass.** Synthetic outputs put 8–33% of mass on *neutral*, while Pew items had no midpoint. Record the response scale of each CIS item and compare like with like.
* **Message-driven topics.** *Judiciary/courts* produced uniform rejection with almost no persona separation. Attribution tests directly whether such patterns follow the rhetoric or the speaker.
* **Single model.** All generations came from gpt-oss-20b. A second model is RQ6.

## 3. Research questions

Core of the first paper:

* **RQ1, speaker attribution.** How does attributing the same political message to Sánchez, Feijóo or Abascal, rather than presenting it anonymously, change its evaluation by synthetic personas?
* **RQ2, ideological congruence.** Does the speaker-attribution effect depend on the persona's elicited ideological self-placement? In particular, do personas respond more positively when the attributed speaker is ideologically closer to them?
* **RQ3, differences across topics.** Does the size or direction of the speaker-attribution effect vary across salient political topics, such as immigration, housing and the cost of living?
* **RQ4, external comparison with CIS.** Do the relative preferences for Sánchez, Feijóo and Abascal among left-, centre- and right-leaning synthetic personas resemble the relative leader evaluations found among the corresponding ideological groups in CIS data? The comparison concerns the general ordering and direction of the preferences, not equality between synthetic and CIS scores.

If time allows, otherwise in a follow-up paper:

* **RQ5, electoral context.** Do leaders' communication and its reception change between normal political periods, pre-election periods, election campaigns and post-election periods? In particular, does the effect of the speaker's name become stronger during election campaigns?

Held in reserve, run only if reviewers ask for it so that it does not take focus from RQ1–RQ4:

* **RQ6, robustness across models.** Are the speaker-attribution, ideological-congruence and topic effects consistent across different LLMs?

The central contribution comes from RQ1 and RQ2. RQ3 shows whether the effect generalises across political issues, and RQ4 provides an external comparison with CIS.

## 4. Actors and sources

| Actor ID | Leader | Party | X account | X posts |
| --- | --- | --- | --- | --- |
| `pedro_sanchez` | Pedro Sánchez | PSOE | @sanchezcastejon | 3,902 |
| `alberto_nunez_feijoo` | Alberto Núñez Feijóo | PP | @NunezFeijoo | 3,515 |
| `santiago_abascal` | Santiago Abascal | VOX | @Santi_ABASCAL | 3,446 |

X counts come from `docs/X_RESULTS.md` (2022-04-01 to 2026-10-02, retweets excluded). Actor IDs are shared across sources (`config/politicians.json`, `config/x_accounts.json`).

* **X.** Official API v2, pay-per-use, full-archive search. The only corpus of this paper. See `docs/X_SCRIPTS.md` and `docs/X_RESULTS.md`.
* **Follow-up paper.** Congreso interventions (`docs/CONGRESO_SCRIPTS.md`, `docs/CONGRESO_RESULTS.md`), La Moncloa transcripts (`docs/MONCLOA_SCRIPTS.md`) and PP media and official documents (`docs/PP_MEDIA_SCRIPTS.md`). Their pipelines stay in the repository. They are left out of this paper because parliamentary speeches contain many clues about the real speaker, which makes a truly anonymous condition hard to build, and because the material is unbalanced across leaders (Congreso: Sánchez 375, Feijóo 184, Abascal 66 segments). The comparison between X, parliament and interviews moves to the follow-up paper with them. Interviews remain a process-only corpus because of the rights questions around Art. 67 of the Spanish copyright law.

Other leaders (Sumar once it has a leader, Podemos, ERC, Junts, EH Bildu, PNV) can be added through the configuration files. Regional leaders often post in other languages, which would need a separate language decision.

## 5. Time windows

* **Collection window.** From 2022-04-01, when Feijóo became PP leader, to 2026-10-02 (collected). Weekly incremental updates until the election.
* **Analysis window [open].** The XV Legislature, from 2023-08-17, as proposed before, or the full collection window. RQ5 needs the full window, because it includes the May 2023 municipal and regional elections and the July 2023 general election.
* **Election periods (RQ5) [open].** Boundaries of the normal, pre-election, campaign and post-election periods, and which elections count (general, municipal and regional, European).

## 6. Corpus construction

1. **Collection.** Raw API responses stored untouched, with query manifests (`docs/X_SCRIPTS.md`).
2. **Normalisation.** Shared internal and stimuli tables per source (`docs/DATA_DICTIONARY.md`).
3. **Quality filters.** Remove retweets (already excluded at query time), decode HTML entities, remove URLs, normalise whitespace, drop very short or low-information posts. Only original posts are used: quotes and replies depend on another author's text, which would either be missing from the stimulus or add a second, often identifiable, author. They stay in the internal table for context but do not become stimuli. Thresholds of the previous paper (40 characters, 7 words), and duplicates removed ([STIMULI.md](STIMULI.md)).
4. **Moral relevance.** Not used. ME2-BERT, the filter of the previous paper, does not support Spanish, and a multilingual alternative or a translation would need its own validation. Posts are selected by topic instead (§7), and the moral-relevance columns of the stimuli table stay empty.
5. **Topic assignment.** Spanish high-precision keyword registry (`config/x_topic_keywords.json`), exactly-one-match rule, `review_needed` for multi-topic hits, as in the previous paper. Results and spot check in [TOPIC_SELECTION.md](TOPIC_SELECTION.md). The stimuli are all the posts on the four topics that pass every check, 1,075, without sampling, as in the previous paper ([STIMULI.md](STIMULI.md)).
6. **Anonymization.** Replace actor names and nicknames, party names and acronyms, campaign slogans and hashtags, and user handles with placeholders (`[POLITICAL_ACTOR]`, `[PARTY]`, `[CAMPAIGN_TAG]`, `[USER]`). Check for identity leaks, degradation and context dependence. The anonymized text is shown in all four conditions. Only the three leaders and their parties are replaced; other politicians' names stay. 7,299 of 7,625 original posts pass the quality and anonymization checks ([STIMULI.md](STIMULI.md)).
7. **Attribution conditions.** The no-author condition shows the anonymized post with a neutral role. Each attributed condition shows the same text with one leader named as its author. **[open]** How the speaker is named (name only, or name and party), and whether institutional roles such as "President of the Government" are shown, since they identify Sánchez.
8. **Cross-attribution checks [open].** Some posts identify their author through their content (e.g., "mi Gobierno", references to the speaker's own party or past acts), so they cannot plausibly be attributed to another leader. Decide whether to filter them or restrict their attributions. A leakage probe, asking the model to guess the author of the no-author version, would measure how anonymous that condition really is. A keyword check of the author's government or opposition position marks 60% of Feijóo's, 31% of Sánchez's and 59% of Abascal's topic posts, and misses many more, so filtering is not an option; a plausibility check (the model judges, for each post and leader, whether that leader could have written it) is preferred, pending Candida's confirmation; options in [STIMULI.md](STIMULI.md).
9. **Unit of judgment [open].** The meeting design shows a single post. Decide whether topic-specific, single-actor bundles are kept as a second level, as in the previous paper, with one attributed author per bundle.

## 7. Topics

The final set is a small number of issues that are salient in Spain during the study period. Each topic must be frequent enough in the data and discussed by all three leaders, so that the leaders are compared on similar messages. This serves two purposes: differences between leaders do not simply reflect that they discuss different issues, and RQ3 can test whether the attribution effect appears across topics or only for particular issues. The topics are selected by their salience in the CIS monthly barometers and their coverage in the posts; the rule and its results are in [TOPIC_SELECTION.md](TOPIC_SELECTION.md). The CIS is not used as a topic-level benchmark (§9).

Final set, selected by the rule on 18 October 2026:

| Topic | Content |
| --- | --- |
| `economy_employment` | growth, unemployment, jobs and wages |
| `household_costs` | housing, rents, energy bills and the cost of living |
| `immigration_border` | arrivals, asylum, regularisation, unaccompanied minors, border control |
| `corruption` | corruption crimes and cases |

The meeting had proposed immigration, housing and the economy and cost of living as examples, and corruption was proposed later. The rule confirms them, with housing and the cost of living as one topic and the economy and employment as another. Health ranks third in the CIS but is dropped because Abascal has only 4 original posts on it.

## 8. Personas

* **Pool.** The persona pool of Greco et al. (2026), not restricted by country. In Candida's preliminary analysis, the country label carried no cultural effect (gpt-oss assigns it almost at random, mostly the United States), and the pool is not intended to represent any real population.
* **Elicited ideological self-placement.** The pool has no left–right variable. Before showing any post, every persona answers the CIS 1–10 left–right self-placement question. It is the model's interpretation of the persona, not ground truth. It is used only to form left, centre and right groups, and the persona's group is never included in the prompts in which it judges messages.
* **[open, Candida]** Which personas from the pool are used: the whole pool, a sample, or the IW/MFT selection strategies of the previous paper.
* **Frame of reference** (decided 2026-11-06). The left–right scale is relative to each country's party system, so the question anchors it to Spanish politics; the personas are not made residents of Spain, as in the reaction prompts. Wording and script: [PERSONA_IDEOLOGY.md](PERSONA_IDEOLOGY.md).
* **Stability** (deferred to December 2026). One elicitation in the preprint; reworded questions and a check without the Spanish anchor are possible extras ([PERSONA_IDEOLOGY.md](PERSONA_IDEOLOGY.md)).
* **[open]** Additional CIS items to make the placement more robust: vote recall and party sympathy, and the placement of PSOE, PP and VOX on the same scale as a check that the persona understands the Spanish scale. Leader ratings are not used for grouping, because they are the outcome compared in RQ4.
* Cut-offs for the left, centre and right groups: 1–4, 5–6 and 7–10, the same for personas (RQ2) and CIS respondents (RQ4).

## 9. External benchmark: CIS

In the previous paper, Pew provided public opinion that could be compared by political topic. The CIS does not provide that kind of topic-level information for this study; it mainly provides general evaluations of Sánchez, Feijóo and Abascal. It is therefore used as a broader check (RQ4), for example:

* Among left-wing personas, does Sánchez's name produce the most positive effect? Among left-wing CIS respondents, is Sánchez also the most positively evaluated leader?
* Do right-wing personas and right-wing CIS respondents show a similar relative preference for Feijóo or Abascal?

The comparison concerns the general pattern, not the exact scores. It does not show that the personas represent Spanish citizens, only whether the synthetic political pattern resembles the pattern found in CIS data.

Items to inventory, with availability checked wave by wave:

* Ratings of and confidence in the three leaders.
* Ideological self-placement (1–10), to form the CIS groups with the same cut-offs as the personas.
* Waves: the 48 barometers from April 2022 to October 2026, the window of the posts, pooled with every wave weighing the same. Method and script: [RQ4_ANALYSIS.md](RQ4_ANALYSIS.md).

Selection follows the Pew protocol of the previous paper: deterministic inventory, inclusion and exclusion codes, weighted distributions, response-scale record, rule trace per item.

## 10. Generation setup

* gpt-oss-20b served with vLLM through an OpenAI-compatible API, temperature 0.01, max 4096 tokens, same hardware as the previous paper.
* A second model only if RQ6 is run, preferably open weights with strong Spanish (e.g., a BSC ALIA/Salamandra model or another multilingual open model).
* **[open]** Prompt language: Spanish messages with the English prompt template, or a validated Spanish translation of the template.

## 11. Analyses

* **RQ1.** For each persona and post, compare stance and labels in each attributed condition with the no-author condition. Models with the condition as predictor and persona and post as random effects (e.g., ordinal mixed-effects models).
* **RQ2.** The RQ1 models with the interaction between attributed leader and persona ideological group. Congruence: whether the shift is more positive when the attributed leader is ideologically closer to the persona. Method and script: [RQ2_ANALYSIS.md](RQ2_ANALYSIS.md).
* **RQ3.** The interaction between attributed leader and topic, with effects estimated within each topic and compared across topics.
* **RQ4.** Within each ideological group, rank the three leaders by their synthetic attribution effect and by their weighted mean CIS evaluation, and compare the rankings and the direction of the differences between leaders. Method and script: [RQ4_ANALYSIS.md](RQ4_ANALYSIS.md).
* **RQ5.** Label posts by election period. Compare the topics and communication of each leader across periods, and add period × attributed leader to the RQ1 models.
* **RQ6.** Re-run RQ1–RQ3 with a second model and compare the direction and size of the effects.

## 12. Ethics, legal and data release

* X data is collected only through the official API under the developer terms. No scraping or account pools.
* Politicians' public statements are still personal data under the GDPR. Processing is limited to research purposes, stored securely, and not used to profile private individuals.
* The attributed conditions show posts under the names of leaders who did not write them. These counterfactual attributions exist only inside the prompts. They are never published as statements by those leaders, and any example in the paper names its real author.
* Released material: code, configuration, rule tables, audit artifacts, post IDs and derived labels. Not released: post text, Congreso caches, interview transcripts or audio.
* A DSA Art. 40(12) data access application is submitted in parallel, but the project does not depend on it.
* Elicited ideological self-placement is the model's interpretation of a persona, not ground truth. Persona groups are not claims about real voters or party electorates.

## 13. Timeline

| Period | Milestone |
| --- | --- |
| Oct 2026 | Topic registry, anonymization and attribution conditions; elicited self-placement; CIS inventory |
| Oct–Nov 2026 | RQ1–RQ3 generations and analyses; RQ4 comparison with CIS |
| Mid-Nov 2026 | Preprint |
| Nov 2026–Jan 2027 | RQ5 if time allows; ARR submission targeting ACL 2027 **[open]** exact cycle |
| Later | Follow-up paper: Congreso and interviews, venue comparison, RQ5 if not included |

## 14. Decisions

Decided at the meeting of 2026-10-07:

* X posts only. Congreso and interviews, and the comparison between venues, go to a follow-up paper.
* RQ1–RQ4 are the core of the paper. RQ5 is included if time allows, otherwise it goes to the follow-up paper. RQ6 is held in reserve in case reviewers ask for it.
* Personas are grouped by elicited ideological self-placement, which is never shown to them when they judge messages.
* CIS is used to compare the ordering of leader preferences by ideological group, not topic-level distributions.

Decided earlier: collection window from April 2022 (collected); preprint in mid-November 2026 and ARR submission targeting ACL 2027; original posts only, without replies and quotes (§6); no moral-relevance filter, because ME2-BERT does not support Spanish (§6); final topic set (§7).

Still open:

1. Analysis window and election periods (§5).
2. How the speaker is named in the attributed conditions, and which author role each condition shows (§6).
3. Cross-attribution plausibility filter and leakage probe (§6).
4. Unit of judgment: single post or also bundles (§6).
5. Persona sampling, additional CIS items and group cut-offs (§8).
6. CIS waves (§9).
7. Prompt language, and the second model if RQ6 is run (§10).
