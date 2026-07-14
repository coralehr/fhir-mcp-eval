# QT-4 v3b microbiology mechanism result

Status: exploratory same-set mechanism result
Completed: 2026-07-13
Questions: 42 microbiology questions, three paired arms
Answer model: `gpt-5.6-sol`, high reasoning effort

## Registered results

| Arm | Correct | Accuracy | Accepted answer tokens |
|---|---:|---:|---:|
| A6a baseline | 7 / 42 | 16.7% | 3,724,515 |
| V: microbiology vocabulary | 25 / 42 | 59.5% | 945,296 |
| T: vocabulary plus bounded traversal | 28 / 42 | 66.7% | 1,002,695 |

- V minus A6a: **+42.9 percentage points**, patient-cluster bootstrap
  95% interval **+20.5 to +63.6**, exact paired McNemar **p=.000277**.
- T minus V: **+7.1 percentage points**, patient-cluster bootstrap 95%
  interval **0 to +15.9**, exact paired McNemar **p=.25**.
- Gold-resource recall moved **3/78 -> 45/78 -> 50/78**. Vocabulary
  supplied 42 of the 47 recovered gold resources; traversal supplied five.
- All 126 answers completed cleanly with zero failed attempts, so accepted and
  all-attempt answer-token totals are identical.

## Interpretation boundary

This pre-declared 42-question mechanism screen came from the already-inspected
409-question set and did not re-answer the 367 controls. Vocabulary produced a
favorable registered micro-slice estimate and advanced only to confirmation;
neither arm is formally promoted until untouched-holdout grading completes.

The result supports terminology-aware packet construction on this slice. The
incremental traversal contrast remained statistically unresolved. It does not
validate Bonfire as a product, graph traversal in general, or any persistent
graph database. No Postgres-versus-graph-engine comparison was run.

## Registered traversal surface

The traversal arm allowed one-hop exact relative references over:

- `Observation.hasMember`
- `Observation.specimen`
- `DiagnosticReport.result`
- `DiagnosticReport.specimen`

The DiagnosticReport paths had zero use in this slice, so the observed path
mechanism is limited to Observation/Specimen.

## Reproducibility receipt

The authoritative sealed controller, deterministic grader, arm-blind panel,
packet hashes, per-answer completion receipts, and final JSON result live in
the v3b artifact directory produced by the registered runner. This document is
an aggregate-only public rendering; it intentionally contains no answer text or
credentialed MIMIC content.
