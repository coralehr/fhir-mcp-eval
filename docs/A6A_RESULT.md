# A6a result — question-only selection beats query-blind projection

*Confirmatory run 2026-07-11/12 · pre-registered in [prereg/A6A.md](prereg/A6A.md)
(v1.0 + Amendment 1, frozen at tag `a6a-freeze-1`) · substrate: Codex CLI
(subscription-billed, version in run manifests) over a self-hosted Medplum
loaded with the MIMIC-IV-on-FHIR demo · one attempt per question, both arms
contemporaneous on the same instance. **Preliminary: single-model,
single-substrate, single-family panel; judge re-measurement
([ROADMAP item 16](ROADMAP.md)) pending — this headline carries that caveat
by the roadmap's ordering gate.***

## The question

Does deterministic **question-only** query-aware selection — plan the FHIR
fetch from the question text, patient ID, and stated current-date assumption
alone, with hard packet bounds — beat **query-blind blunt projection**
(strip + per-type recency-cap 50) at the same substrate, instance, prompts,
schema, and grading? This isolates one treatment: *what gets selected into
the packet*. The planner is a deterministic keyword/date parser (`qo-v2`) —
a floor, not a product; it reads none of the benchmark's construction
metadata (whitelist enforced and tested).

## Primary result (all 409 test questions, canonical grading)

| arm | pooled accuracy |
|---|---|
| **A6a — question-only selection** (bounds 200 res / 160k chars) | **54.3%** |
| **A0′ — blunt projection** (per-type recency-cap 50) | **44.7%** |

- Difference **+9.5pp**, patient-cluster bootstrap 95% CI **[+5.4, +13.7]**
  (90 clusters, 10k reps, seeded)
- Discordant pairs **59 vs 20**; exact paired McNemar **p = 1.3×10⁻⁵**
- Grading: deterministic numeric/unanswerable rules (lifted from the
  trustworthy pipeline) for 129 paired labels; 3-vote arm-blind codex panel
  for the 548 boolean/categorical/other labels; failures score 0

Per the pre-registered decision rule, the licensed claim is:
**"Deterministic question-only selection beats query-blind projection."**
The selection lever survives removal of every oracle shortcut.

**Not licensed (and not claimed):** any sandbox comparison (no A5-equivalent
has rerun under these controls); any serialization, coverage, or product
claim; any cross-benchmark comparison.

## Economics (secondary)

Same direction as accuracy: A6a used **43% less packet payload** in total
(36.7M vs 64.0M chars; medians 135k vs 154k) and abstained less often
(173 vs 210 of 409). Marginal model cost of the full experiment — 818
answers + 1,644 panel votes — was $0 beyond the Codex subscription.

## Where the win lives (exploratory strata — CIs not shown, no stars)

| stratum | n | A6a | A0′ |
|---|---|---|---|
| numeric golds | 97 | 43.3% | 23.7% |
| labevents | 63 | 61.9% | 33.3% |
| chartevents | 77 | 33.8% | 16.9% |
| prescriptions | 63 | 58.7% | 44.4% |
| boolean golds | 115 | 52.2% | 47.8% |
| unanswerable (correct abstention) | 57 | 68.4% | 66.7% |
| admissions / icustays / patients | 76 | ≈ tie | ≈ tie |
| **microbiologyevents** | 42 | **14.3%** | **9.5%** |

Selection wins where retrieval precision matters (single right value in a
large record); it ties where a recency cap already suffices (admissions,
demographics). Microbiology is a shared failure — a recall problem in both
arms and the top target for the next planner version, not a
selection-vs-blunt question.

## Honest limitations

1. **Planner floor:** qo-v2 is deterministic keywords + date regexes. Its
   measured dev-slice packet recall ceiling was ~75%; 54.3% pooled accuracy
   says the next gains are in recall, not model reasoning.
2. **Single model, single substrate, single instance.** No claim of
   generality across models or FHIR servers.
3. **Panel is single-family** (3-vote codex), the same
   conservative-lower-bound convention this fork used for A0′ non-numeric
   labels; cross-family adjudication is ROADMAP item 15.
4. **Judge re-measurement pending** (item 16): the deterministic subset is
   judge-free, but panel-graded labels inherit LLM-judge risk. The
   judge-reliability finding of the parent study is precisely why this
   caveat is named rather than waved at.
5. **Interim look disclosed:** at 86% completion a deterministic-subset
   interim look was computed at the investigator's request (recorded in the
   prereg's Deviations); no design change followed.
6. **A0′-as-frozen-packet** differs from the historical multi-turn A0′; the
   historical 39.4% is not comparable and is not used.

## Reproducibility

Freeze tag `a6a-freeze-1`. Packet manifests with SHA-256 hashes
(`runs/a6a_test409_manifest.json`, `runs/a0prime_test409_manifest.json`),
per-question prompts/events/answers under `runs/codex-*-test409/`,
deterministic verdicts + panel votes + assembled result under
`runs/a6a-confirmatory-grading/` (`final_result.json` carries the seeded
bootstrap config). Rerun the assembly:
`python3 final_confirmatory_result.py`.
