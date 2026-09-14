# Spanish Political Communication

Computational research on political communication in Spain, integrating social media posts, parliamentary interventions, media interviews, and other publicly available political discourse.

## Overview

This project aims to build a longitudinal, multimodal corpus of political communication produced by three major Spanish political figures:

* Pedro Sánchez
* Santiago Abascal
* Alberto Núñez Feijóo

The project will collect and analyse publicly available political discourse across different communication environments, with the objective of studying how political actors communicate, frame issues, construct narratives, and employ different forms of political and moral language across contexts.

The initial focus will be on material available from X, parliamentary interventions, and media interviews, although additional sources may be incorporated where they are sufficiently accessible, reliable, and relevant to the research objectives.

## Research objectives

The project will investigate, among other aspects:

* Political framing and the ways political actors present issues, events, groups, and political opponents.
* Moral language and moral rhetoric, including the use of moral concepts and values in political discourse.
* Political narratives and recurring rhetorical patterns.
* Topic and semantic patterns across political actors and communication environments.
* Temporal changes in political communication.
* Differences between communication contexts, particularly social media, parliamentary discourse, and traditional media.
* Differences and similarities between political actors in their communication strategies.
* The relationship between political events and changes in political discourse.

The specific research questions and analytical dimensions will be refined as the corpus is constructed and the available data are assessed.

## Data sources

The project will prioritize publicly available sources, including:

### X

Posts published by the official accounts associated with the three selected political figures, subject to the availability and applicable terms of the relevant APIs and data-access mechanisms.

### Parliamentary interventions

Publicly available interventions in the Spanish parliamentary context, including relevant speeches, debates, questions, replies, and other parliamentary contributions where structured or reliable source data are available.

### Media interviews

Interviews and other relevant appearances in publicly accessible media sources, subject to availability, licensing, accessibility, and the possibility of reliably obtaining and processing the underlying textual material.

### Additional sources

Other sources may be incorporated when they provide relevant, publicly available material that contributes to the longitudinal analysis of political communication.

All sources will be documented to facilitate reproducibility and provenance tracking.

## Methodology

The project will use methods from Natural Language Processing (NLP) and computational social science.

Potential analytical approaches include:

* Text preprocessing and normalization
* Topic modelling and topic classification
* Supervised and weakly supervised text classification
* Framing analysis
* Moral language and moral foundations analysis
* Sentiment and emotion analysis
* Semantic similarity and embedding-based analysis
* Narrative analysis
* Temporal analysis
* Comparative analysis between political actors and communication contexts
* Manual annotation and validation of computational models
* Large Language Model (LLM)-assisted annotation and analysis where appropriate

Automated analyses will be evaluated against manually annotated samples whenever appropriate.

## Corpus structure

The intended corpus will preserve information about both the content and provenance of each document.

A conceptual representation is:

Political actor
    │
    ├── Social media
    │      └── X posts
    │
    ├── Parliament
    │      └── Parliamentary interventions
    │
    └── Media
           └── Interviews / appearances

Each document should retain, where available, metadata such as:

* Political actor
* Date and time
* Source/platform
* Document type
* Original URL or source identifier
* Text
* Event/context
* Language
* Relevant metadata
* Data collection date
* Provenance information

The exact schema will evolve as the different data sources are integrated.

## Reproducibility

The repository is intended to support reproducible computational research.

Code will be organized into components for:

1. Data collection
2. Data validation and provenance
3. Preprocessing
4. Corpus construction
5. Annotation
6. NLP analysis
7. Statistical analysis
8. Visualization
9. Research outputs

Raw data will only be stored or redistributed when permitted by the terms applicable to the corresponding source. Where redistribution of source content is restricted, the repository will prioritize reproducible code, derived data where permissible, document identifiers, metadata, and procedures for reconstructing the corpus.

## Ethical and legal considerations

The project focuses primarily on public political communication produced by public political figures.

The research is intended for academic, non-commercial purposes. Data collection and analysis will be conducted in accordance with:

* Applicable data protection legislation
* Institutional research ethics requirements
* Terms of service and API policies of individual platforms
* Copyright and database-rights requirements
* Applicable restrictions concerning the storage and redistribution of source material

The project does not aim to profile individual members of the public or infer sensitive personal characteristics from private individuals.

## Research scope

The initial corpus will focus on Pedro Sánchez, Santiago Abascal, and Alberto Núñez Feijóo. The temporal scope is expected to be longitudinal, allowing the analysis of changes in political communication across different political periods and relevant political events.

The precise temporal boundaries will be established as data availability and research questions are further defined.

## Project status

Status: Initial development

The project is currently in the corpus-design and data-acquisition stage. The repository structure, data schema, collection procedures, and analytical methodology will evolve as the different sources are evaluated and integrated.

## Citation

A formal citation for the dataset, code, and resulting research publications will be added as the project develops.

## License

The licensing of individual components of this repository will be specified according to the nature and provenance of the material. Source data may be subject to separate terms and restrictions.
