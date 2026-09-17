# Congreso de los Diputados results

This document reports only information contained in
`congreso_summary.json` and `congreso_extraction_summary.json`.

## Inventory collection

### Source snapshot

| Field | Value |
| --- | --- |
| Source URL | `https://www.congreso.es/webpublica/opendata/intervenciones/IntervencionesCronologicamente__20260914050120.json` |
| SHA-256 | `a9616423813f2e5837b1306a9a65eab11168808ca91c4693c5de4927522f1225` |
| Retrieved at (UTC) | `2026-09-14T20:14:23+00:00` |
| Start-date filter | `null` |
| End-date filter | `null` |

### Inventory counts

| Metric | Value |
| --- | ---: |
| Total source rows | 41,339 |
| Invalid dates among matching rows | 0 |
| Unique interventions | 463 |
| Unique full-text documents | 63 |

### Counts by actor

| Actor ID | Matched rows before deduplication | Unique interventions |
| --- | ---: | ---: |
| `alberto_nunez_feijoo` | 165 | 120 |
| `pedro_sanchez` | 351 | 286 |
| `santiago_abascal` | 96 | 57 |

### Date range and approximate duration

| Actor ID | Start date | End date | Approximate duration (hours) |
| --- | --- | --- | ---: |
| `alberto_nunez_feijoo` | 2023-09-26 | 2026-06-24 | 15.83 |
| `pedro_sanchez` | 2023-11-15 | 2026-06-24 | 41.50 |
| `santiago_abascal` | 2023-09-26 | 2026-06-24 | 8.07 |

## Text extraction

| Field | Value |
| --- | --- |
| Parser version | 1 |
| Matching method | Formal uppercase header plus all canonical surname components |

### Extraction counts

| Metric | Value |
| --- | ---: |
| Documents considered | 63 |
| Documents failed | 0 |
| Documents without target segments | 0 |
| Documents with count mismatch | 47 |
| Raw speaker candidates seen | 13,338 |
| Formal speaker turns seen | 13,313 |
| Segments extracted | 625 |

### Segments by actor

| Actor ID | Segments |
| --- | ---: |
| `alberto_nunez_feijoo` | 184 |
| `pedro_sanchez` | 375 |
| `santiago_abascal` | 66 |

### Speaker headers by actor

| Actor ID | Speaker header | Count |
| --- | --- | ---: |
| `alberto_nunez_feijoo` | `NUÑEZ FEIJÓO` | 7 |
| `alberto_nunez_feijoo` | `NÚÑEZ FEIJÓO` | 162 |
| `alberto_nunez_feijoo` | `NÚÑEZ FEIJÓO (candidato a la Presidencia del Gobierno)` | 15 |
| `pedro_sanchez` | `PRESIDENTE (Sánchez Pérez-Castejón)` | 1 |
| `pedro_sanchez` | `PRESIDENTE (Sánchez PérezCastejón)` | 1 |
| `pedro_sanchez` | `PRESIDENTE DEL GOBIERNO (Pérez Sánchez-Castejón)` | 11 |
| `pedro_sanchez` | `PRESIDENTE DEL GOBIERNO (Pérez SánchezCastejón)` | 3 |
| `pedro_sanchez` | `PRESIDENTE DEL GOBIERNO (Sánchez Pérez Castejón)` | 3 |
| `pedro_sanchez` | `PRESIDENTE DEL GOBIERNO (Sánchez Pérez-Castejón)` | 306 |
| `pedro_sanchez` | `PRESIDENTE DEL GOBIERNO (Sánchez PérezCastejón)` | 30 |
| `pedro_sanchez` | `SÁNCHEZ PÉREZ-CASTEJÓN (candidato a la Presidencia del Gobierno)` | 19 |
| `pedro_sanchez` | `SÁNCHEZ PÉREZ-CASTEJÓN (candidato a la presidencia del Gobierno)` | 1 |
| `santiago_abascal` | `ABASCAL CONDE` | 66 |

### Extraction note

The extraction summary states that the stimuli are pseudonymised, not
anonymous, because the exact text remains searchable. It also states that
count mismatches compare Open Data index entries with all formal turns in the
full *Diario* and are quality-assurance warnings rather than automatic
extraction failures.
