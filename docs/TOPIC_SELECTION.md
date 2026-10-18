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

### Renamed labels

The CIS slightly rewords some categories during the window, for example "La subida de impuestos" and "La subida de los impuestos". Without merging them, each wording would compete separately with half of the months. `label_aliases` in the config merges the wordings of the same category. It was added after the download, because the wordings were only known then. It is a technical correction, not a mapping decision: it only affects categories that compete on their own, and the ranking is the same without it (see [Robustness](#robustness)).

Two housing categories, "Las hipotecas" and "Los desahucios", appear in some barometers and are not in the mapping fixed beforehand. They were left out to keep the mapping as fixed; adding them changes the mean of household costs from 22.9 to 23.0.

## Running

```bash
python src/collect_cis_problems.py   # about 100 requests to cis.es, a few minutes
python src/select_cis_topics.py      # ranking, no network
```

`collect_cis_problems.py` finds each barometer at `https://www.cis.es/es/estudios/barometro-de-<month>-<year>`, or on the CIS homepage when the newest one is only published as a results preview. It downloads the results PDF (`es<study>mar`) and parses the main-problems table. PDFs are kept and reused on later runs.

| Output | Content |
| --- | --- |
| `data/raw/cis/es<study>mar.pdf` | Results PDF of each barometer, as published |
| `data/processed/cis_problems_monthly.csv` | One row per barometer and CIS category: first, second, third and TOTAL percentages, N, source URL |
| `data/processed/cis_topic_ranking.csv` | Mean, peak and member categories of every topic, standalone category and exclusion group |
| `data/processed/cis_category_assignment.csv` | Where each CIS category went: excluded, topic or standalone |

CIS data may be reused with attribution to the CIS ([conditions](https://www.cis.es/en/conditions-reusing-cis-data)). Cite the barometers as studies 3359 to 3582.

## Results

Run on 18 October 2026.

**Coverage.** 46 barometers from April 2022 to October 2026. Nine months have no usable barometer:

* August of every year (2022 to 2026) and June and September 2023, around the general election: no barometer was published.
* May 2023 (study 3405): the table is printed transposed, with categories as columns, and the parser does not read it.
* December 2024 (study 3489): no results PDF is published.

Each parsed table was checked: the TOTAL percentages of a barometer add up to between 252.6 and 274.5, as expected when most respondents name two or three problems.

**Ranking** (mean of the TOTAL percentage over the 46 barometers):

| Rank | Topic | Mean % | Peak % (month) | Selected |
| ---: | --- | ---: | --- | --- |
| 1 | `economy_employment` | 63.2 | 92.5 (2023-04) | yes |
| 2 | `household_costs` | 22.9 | 57.7 (2026-10) | yes |
| 3 | `health` | 14.6 | 30.5 (2022-07) | yes |
| 4 | `immigration_border` | 13.8 | 30.4 (2024-09) | yes |
| 5 | `corruption` | 8.3 | 25.3 (2025-07) | yes |
| 6 | La educación | 7.1 | 16.5 (2022-11) | |
| 7 | Los problemas relacionados con la juventud | 7.0 | 12.7 (2025-10) | |
| 8 | Las desigualdades, incluida la de género, las diferencias de clases, la pobreza | 6.9 | 17.8 (2024-06) | |
| 9 | `environment_emergencies` | 6.3 | 16.4 (2024-02) | |
| 10 | La inseguridad ciudadana | 4.6 | 8.8 (2022-11) | |
| 11 | `territorial_model` | 3.5 | 9.2 (2023-12) | |
| 13 | `foreign_policy_war` | 3.2 | 6.7 (2022-10) | |
| 17 | `judiciary` | 1.9 | | |
| 19 | `gender_equality` | 1.1 | | |
| | Excluded: political | 62.1 | 74.8 (2023-12) | |
| | Excluded: diffuse | 15.8 | 26.9 (2024-06) | |
| | Excluded: non-substantive | 5.6 | 11.5 (2022-05) | |

Merged topics are sums of their categories, so their means are upper bounds (see [Parameters and why](#parameters-and-why)). The full ranking is in `data/processed/cis_topic_ranking.csv`.

The three topics proposed by the team (household costs, immigration and corruption) are all selected. The rule adds two more: the economy and employment, and health.

### Robustness

| Variant | Top 5 |
| --- | --- |
| As fixed | economy_employment, household_costs, health, immigration_border, corruption |
| Without `label_aliases` | Same |
| Health without the COVID-19 categories | Same (health falls to 4th) |
| "Las hipotecas" and "Los desahucios" added to household costs | Same |
| Last 12 barometers only | Same (immigration 3rd, corruption 5th) |
| `economy_employment` split into its three CIS categories | Unemployment and job quality enter separately; corruption falls to 7th |

The result depends on one decision: merging the economy, unemployment and job quality into one topic. That merge was part of the rule fixed before the download (commit `9b5c035`), and it matches the keyword registry, where the three are hard to separate in short posts.

### Next step

Coverage (rule step 5) needs the keyword registry for the five selected topics. A topic is kept only if each leader has at least 10 original posts on it.
