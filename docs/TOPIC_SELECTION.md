# Topic selection

This document defines how the topics for RQ3 are chosen. The rule was fixed on 18 October 2026, before any CIS data were downloaded, and is stored in `config/cis_topic_selection.json`. The scripts read the parameters from that file, so this document and the code cannot drift apart.

The aim is that the topics follow from public salience in Spain and from what the three leaders actually post about, not from the team's own preferences. Topics proposed at the meeting of 7 October 2026 or by the team are hypotheses that the rule tests, not inputs to it.

## Rule

1. **Public salience.** For every CIS monthly barometer from April 2022 to October 2026, take the question on the three main problems of Spain and the TOTAL column: the percentage of respondents who name each category as any of their three problems.
2. **Exclusions.** Drop the categories listed in [Exclusions](#exclusions).
3. **Mapping.** Merge the CIS categories that belong to the same topic, as listed in [Mapping](#mapping). The topic's value in a barometer is the sum of its categories. A category that is neither excluded nor mapped competes on its own.
4. **Ranking.** Average each topic over all barometers in the window and keep the **top 5**.
5. **Coverage.** Assign the X posts to topics with the keyword registry (exactly one match per post, original posts only). Keep a top-5 topic only if **each of the three leaders has at least 10 original posts** on it.

No topic is added outside the rule. If a topic proposed by the team is not selected, this is reported, not overridden.

## Parameters and why

| Parameter | Value | Why |
| --- | --- | --- |
| Source | CIS Barómetro, monthly | The official, regularly repeated measure of issue salience in Spain, published free with a stable question wording. |
| Question | Three main problems of Spain, spontaneous answer | It asks about the country, which matches political communication better than the question about the problems that affect the respondent personally. |
| Measure | TOTAL column (any of the three mentions) | The first-mention column alone hides issues that are important but rarely first, and TOTAL is the figure the CIS and the press report. |
| Window | 2022-04 to 2026-10 | The same window as the X corpus, so salience and posts refer to the same period. |
| Aggregation across barometers | Mean | Every month weighs the same, as the posts are spread over the whole window. The peak month is reported for context but not used. |
| Aggregation within a topic | Sum of its categories | Without microdata the share of respondents naming any category of a topic cannot be computed exactly. The sum is an upper bound, because one respondent can name two categories of the same topic. Merges are therefore kept few and close. |
| k | 5 | The previous paper used five issue domains. Each extra topic needs its own keywords and thins the posts per topic, and some leader × topic cells are already small. |
| Minimum posts | 10 original posts per leader | Each post is judged under all three attributed names, so a thin cell does not affect the attribution comparison; it only limits estimates by real author. Ten is a floor that still gives a per-leader mean. This value was set after a draft count had shown 16 posts for Abascal on household costs, so it is stated here openly. |

## Exclusions

| Group | CIS categories | Why |
| --- | --- | --- |
| Political | Los problemas políticos en general; El Gobierno y partidos o políticos/as concretos/as; El mal comportamiento de los/as políticos/as; Lo que hacen los partidos políticos; La falta de acuerdos, unidad y capacidad de colaboración; La falta de confianza en los políticos y las instituciones; El funcionamiento de la democracia; La clase política; Los extremismos | They name the political class, the parties, the government or the state of politics as the problem, not a policy area. They also overlap with what RQ1 measures, the evaluation of politicians. Leaders' posts of this kind (rallies, thanks to members, attacks on rivals) go to the residual category. |
| Diffuse | La crisis de valores; Los problemas de índole social; Las incertidumbres ante el futuro; Poca conciencia ciudadana; Aumento de la crispación social | No identifiable policy area that a keyword registry could match. |
| Non-substantive | Otras respuestas; Ninguno; N.S.; N.C.; (N) | Not problem categories. |

## Mapping

The merged topics follow the candidate topics of `docs/PROPOSAL.md` §7, so that a CIS category and an X post on the same issue end up in the same topic.

| Topic | CIS categories merged |
| --- | --- |
| `household_costs` | La vivienda; La subida de tarifas energéticas, crisis energética; any category on prices, inflation or the cost of living |
| `economy_employment` | La crisis económica, los problemas de índole económica; El paro; Los problemas relacionados con la calidad del empleo |
| `immigration_border` | La inmigración; Crisis con Marruecos, fronteras de Ceuta y Melilla |
| `corruption` | La corrupción y el fraude |
| `health` | La sanidad; Los peligros para la salud: COVID-19 |
| `foreign_policy_war` | Conflicto de Ucrania-Rusia; Las guerras en general; Política exterior y relaciones internacionales; categories on the Middle East |
| `territorial_model` | Los nacionalismos; Los estatutos de autonomía; La independencia de Cataluña; any category on the amnesty |
| `environment_emergencies` | El medio ambiente; El cambio climático; Los incendios; any category on the DANA, floods, drought or the blackout |
| `gender_equality` | La violencia de género |
| `judiciary` | La Administración de Justicia |

Two choices deserve a note:

* **The general economy is not part of household costs.** In the two barometers inspected before the rule was fixed (April 2022 and October 2026), the CIS list has no separate category for prices or inflation, so such answers are probably coded under "La crisis económica". Household costs therefore only receive the categories that name housing, energy bills or prices, and its salience may be underestimated.
* **Squatting stays apart from housing.** "La ocupación de viviendas" is about property and security, not about the cost of housing, so it competes on its own.

The patterns that implement the mapping are in `config/cis_topic_selection.json`. Labels are matched after lower-casing and removing accents, exclusions first, then the topics in the order listed, and the first match wins.

## Results

To be filled in after running the selection.
