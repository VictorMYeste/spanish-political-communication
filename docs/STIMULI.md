# Preparing the X stimuli

Steps 3 (quality filters), 6 (anonymization) and 8 (cross-attribution check) of [PROPOSAL.md](PROPOSAL.md), section 6. Rules: `config/x_stimulus_filters.json`. Code: `src/prepare_x_stimuli.py`.

```bash
python src/build_x_tables.py      # x_stimuli.csv with original posts only
python src/assign_x_topics.py     # fills topic
python src/prepare_x_stimuli.py   # cleans and anonymizes text_anon, writes x_stimuli_qa.csv
```

| Output | Content |
| --- | --- |
| `data/processed/x_stimuli.csv` | `text_anon` holds the cleaned and anonymized text. `keep_for_prompt` stays `False`; it is set when the sample is drawn |
| `data/processed/x_stimuli_qa.csv` | One row per stimulus: `exclude_reason`, `role_groups`, `role_terms`, `implausible_under` |

## Quality filters

The text is cleaned first: HTML entities decoded, URLs removed, whitespace collapsed. The thresholds are those of the previous paper (filter spec 1.2.0), counted on the cleaned text:

* `too_short`: fewer than 40 characters.
* `too_few_words`: fewer than 7 words.

Two rules of the previous paper are not used. Its list of exact low-information texts ("thank you", "so true"…) is not needed, because such texts are already below 7 words. Its truncation flag ("...") does not apply: the X API returns the full text, including long posts, and in Spanish the ellipsis is usually stylistic (154 posts).

## Anonymization

As in the previous paper, only the leaders and their parties are replaced, and the same text is shown in all four conditions:

| Placeholder | Replaces |
| --- | --- |
| `[POLITICAL_ACTOR]` | The three leaders: full names, surnames, "Pérez-Castejón", "sanchismo", "sanchista", and their own accounts |
| `[PARTY]` | PSOE, PP, VOX and their regional branches (PSC, PSdeG, PSPV…), "Partido Popular (Europeo)", "Partido Socialista (Obrero Español)", PPE; party accounts (`@ppopular`, `@PSOE_M`, `@vox_es`…) and European party families (`@EPP`, `@PatriotsEU`, `@soc_intl`…) |
| `[CAMPAIGN_TAG]` | Hashtags with a leader's or party's name (#VotaPSOE, #InvestiduraFeijóo, #SánchezDimisión), and 54 party slogans chosen by hand from the 203 hashtags used at least 3 times (#EsElMomento, #ElGobiernoDeLaGente, #SentidoComún…) |
| `[USER]` | Every other account, except 27 public institutions that keep a plain name (`@Congreso_Es` → "Congreso de los Diputados", `@NATO` → "OTAN") |

Other politicians' names stay (Ábalos, Ayuso, Puigdemont, Trump): replacing them would empty many posts, especially on corruption, and the plausibility check covers the cases in which they give the author away. Bare first names stay too, because "Pedro" is mostly someone else (Pedro Rollán, Pedro Solbes). Hashtags about content, places and dates stay (#DANA, #COP28, #Badajoz, #23J), as do party-adjective words like "socialistas".

Two checks follow, with the previous paper's thresholds:

* `identity_leak`: a leader's or party's name is still there, for example a misspelling or a missing space ("golpe deSánchez").
* `anonymization_degraded`: after anonymization, fewer than 40 characters, fewer than 7 words not counting placeholders, or more than 3 placeholders in 80 characters or less.

Finally, `duplicate_text` marks a post whose anonymized text, ignoring case, repeats an earlier one that passed every check.

## Results

| | Feijóo | Sánchez | Abascal |
| --- | ---: | ---: | ---: |
| Original posts | 3,032 | 3,295 | 1,298 |
| `too_short` | 90 | 31 | 90 |
| `too_few_words` | 90 | 31 | 137 |
| `identity_leak` | 0 | 0 | 2 |
| `anonymization_degraded` | 109 | 36 | 154 |
| `duplicate_text` | 6 | 7 | 10 |
| **Pass every check** | **2,915** | **3,252** | **1,132** |

A post can fail several checks; most `anonymization_degraded` posts were already too short before anonymization, and only 24 fail because of it alone (event announcements such as "Este viernes 28 de abril en #Badajoz [CAMPAIGN_TAG]"). The excluded posts are mostly greetings, slogans and image-only posts ("¡Gracias Ciudad Rodrigo!", "Teníamos razón."); only 8 of the 326 have one of the four topics.

Of the posts that pass, about half contain a placeholder: Feijóo 1,516 of 2,915, Sánchez 1,348 of 3,252, Abascal 747 of 1,132. `[POLITICAL_ACTOR]` appears in 661, 157 and 283 of them; it is almost always Sánchez in the posts of the other two.

## Cross-attribution check

Every post is shown under all three names. Some posts reveal the real author's position, so they are implausible under another name: Sánchez speaking for the Government cannot be Feijóo or Abascal, and Feijóo or Abascal attacking the Government cannot be Sánchez. Anonymization removes names, not these positions.

Role markers are matched like the topic keywords (URLs and accents removed, lower case), only in the posts of the authors each group applies to. Regional and foreign governments and foreign prime ministers are removed before matching.

| Group | Authors | Implausible under | Examples |
| --- | --- | --- | --- |
| `government_voice` | Sánchez | Feijóo, Abascal | "Gobierno", ministers, Consejo de Ministros, EU presidency, summits, "homólogo" |
| `against_government` | Feijóo, Abascal | Sánchez | "Gobierno", ministers, "Sánchez", "sanchismo", resignation, motion of censure |
| `against_right` | Sánchez | Feijóo, Abascal | "ultraderecha", "la derecha", "reaccionarios" |
| `against_left` | Feijóo, Abascal | Sánchez | "izquierda", "comunistas", "progres" |
| `against_pp` | Abascal | Feijóo | "PPSOE", "bipartidismo", "derechita", "populares y socialistas" |

Posts that pass every check, marked / total:

| Topic | Feijóo | Sánchez | Abascal |
| --- | ---: | ---: | ---: |
| `economy_employment` | 33 / 99 | 83 / 304 | 10 / 30 |
| `household_costs` | 60 / 94 | 41 / 123 | 4 / 11 |
| `immigration_border` | 22 / 40 | 17 / 50 | 19 / 56 |
| `corruption` | 101 / 127 | 15 / 27 | 91 / 114 |
| Four topics | 216 / 360 (60%) | 156 / 504 (31%) | 124 / 211 (59%) |
| All posts | 1,189 / 2,915 | 788 / 3,252 | 413 / 1,132 |

### What a reading of the flags shows

Read by Claude on random samples, not a formal annotation:

* **Precision is high.** In 60 marked posts read before the last fixes, 54 showed the author's position; the errors were regional governments, foreign prime ministers, "derecha" as a direction and Feijóo's condolences to socialists, all fixed. In 16 marked topic posts after the fixes, 16 did.
* **Recall is low.** In 12 unmarked topic posts by Sánchez, most still spoke as the Government without a marker word ("vamos a destinar 2.275 M€", "España ha logrado un acuerdo con la OTAN"). In 12 by Feijóo or Abascal, about half still opposed it ("crear una línea telefónica no iba a solucionar el problema de la vivienda"), some of them caught by markers added afterwards ("dimisiones", naming Sánchez).

The markers are a lower bound. The government–opposition position is part of most posts: the head of government announces policy, and the opposition criticises it.

### What it means for the design [open]

Filtering on the markers is not an option. It would leave 7 Abascal posts on household costs (below the minimum of 10), 26 and 23 Feijóo and Abascal posts on corruption, and it would still keep many implausible posts. The options are:

1. **Plausibility check (preferred by Víctor, pending Candida's confirmation).** For each sampled post and each leader, ask the model whether that leader could plausibly have written it. That is three short calls per sampled post. It works as a manipulation check: the main analysis keeps all posts, and a robustness analysis keeps only the plausible post–leader pairs. The marker flags stay as a transparent lower bound.
2. **Only plausible attributions.** Show each post only under the leaders who could have written it. The design is then no longer fully crossed: posts by Sánchez that speak for the Government would only be shown anonymously and under his name.
3. **Flags as a covariate.** Keep the fully crossed design and use the marker flag in the models. Cheapest, but the flag misses many posts.

The leakage probe of PROPOSAL.md (asking the model who wrote the anonymous version) measures something different: it also identifies authors through ideology ("deportaciones ya" points to Abascal), which is part of the message, not an implausible attribution.

## Sample

`src/sample_x_stimuli.py` draws the same number of posts for each topic and real author from the posts that pass every check, or all of them where a cell has fewer (seed 20261019). As in the previous paper, `other` and `review_needed` are left out by default; `--other-per-author` adds posts without a topic. There is no moral-relevance filter (step 4): ME2-BERT does not support Spanish.

```bash
python src/sample_x_stimuli.py --per-cell 20 --personas 100 --dry-run   # counts and model calls, writes nothing
python src/sample_x_stimuli.py --per-cell 20                             # sets keep_for_prompt, writes the manifest
```

The sampled posts get `keep_for_prompt = True`. `data/processed/x_sample_manifest.json` records the parameters, the eligible and sampled counts and the sampled message IDs, so the sample can be published without the text.

| Posts per cell | Posts | Plausibility check | Persona judgments per 100 personas |
| ---: | ---: | ---: | ---: |
| 10 | 120 | 360 | 48,000 |
| 20 | 231 | 693 | 92,400 |
| 27 | 308 | 924 | 123,200 |

Abascal has 11 posts on household costs, so that cell is complete from 11 on. 27 is the largest size that fills every other cell (Sánchez has 27 posts on corruption). The size is not fixed yet: it depends on the number of personas and the time available on the GPU **[open, Candida]**.
