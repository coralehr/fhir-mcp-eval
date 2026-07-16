# A11b event compiler development gate

Status: **zero-model development slice implemented; not an efficacy corpus or
sealed run**.

`a11b_event_compiler.py` owns one deep interface for the registered A11b
representation contrast. It consumes one governed flat traversal, the raw
question, its frozen question-only plan, and one shared packet-byte bound. It
derives all three model payloads together:

- **T0:** flat resources plus replayed model-visible path citations;
- **T1:** byte-identical T0 evidence plus canonical temporal selection,
  path-completeness requirements, and an answerability receipt; and
- **E1:** byte-identical T1 plus a reference-only typed event-group index.

The compiler receipt binds the shared evidence and citation hashes, the shared
T1/E1 selection and answerability hashes, every arm payload hash/byte count,
the common bound, and `model_calls: 0`. If any arm exceeds the bound, none is
sealed.

## Fail-closed rules in this slice

- The plan is rederived from the raw question; a hash-shaped caller plan is not
  trusted.
- Full timestamps require a valid timezone and explicit FHIR component/offset
  ranges. Unsupported sub-microsecond precision, date-only values, missing or
  conflicting effective choices, reversed/open periods, and ambiguity at the
  queried temporal extreme never select an event.
- `first` compares `effectivePeriod.start`; `latest` compares
  `effectivePeriod.end`. Ties outside the queried extreme do not force a false
  abstention.
- Every path step is replayed against its actual FHIR JSON pointer, registered
  source-field-target shape, target type/version, and chain. An unavailable
  terminal must be the exact redacted `{"display":"Reference withheld"}`
  sentinel; its identifier cannot enter any arm.
- Gold, audit/checker/governance fields, explicit arm labels, non-JSON
  containers, and benchmark-derived labels are recursively rejected. The
  existing answer harness now also rejects T0/T1/E1 arm labels and audit
  namespaces.

Synthetic source and audit gold are physically separate:

- `fixtures/a11b_event_compiler_dev.json`
- `fixtures/a11b_event_compiler_dev_gold.json`

The fixture exercises three-event unique selection, a nonselected tie, a tie at
the queried extreme, date-only and timezone ambiguity, unsupported precision,
missing/conflicting time, reversed/open/overlapping periods, issued-time
disagreement, and a selected unavailable path. It contains no PHI.

## What remains before a token can be spent

This slice does **not** build or inspect the untouched efficacy split. The next
builder must pin a new Synthea release/JAR/seed/config, keep development and
patient-disjoint efficacy patients separate, derive sample size from a frozen
patient-cluster power analysis, and prove identical packets in two no-model
builds. The resulting dataset, preregistration, materializer, grader,
controller, executor install receipt, and external exact-head anchor must all
be independently approved before any A11b answer or panel call.
