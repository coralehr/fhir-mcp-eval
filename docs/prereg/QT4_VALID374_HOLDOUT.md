# Pre-registration: QT-4 untouched valid-split holdout

**Status:** FROZEN before any successful packet artifact or answer generation · v1.1 ·
2026-07-13

This is the confirmatory follow-up earned by the sealed exploratory QT-4 v3b
microbiology screen. It evaluates two ordered, already-frozen mechanisms:
question-only fixed microbiology vocabulary and the incremental addition of
bounded exact-reference traversal. It does not select or evaluate a persistent
graph database.

## Frozen data boundary

The source dataset SHA-256 is
`e2045692fef7f5f4f77496935160f5fc727e162d213e94feed61401948e512a0`.
Its `valid` split contains 424 questions. The preserved A6a development
manifest proves that the first 50 valid rows were the reused planner-tuning
slice. The holdout is every other valid row: **374 questions across 90
patients**.

Selection reads only split membership and the preserved development-manifest
question IDs. It does not read answers, gold FHIR IDs, source table labels, or
any arm output. Order is ascending
`sha256("qt4-valid374-20260713:" + question_id)`, then question ID. The exact
IDs are frozen in [`qt4_valid374_spec.json`](qt4_valid374_spec.json); the exact
input rows are frozen in
[`valid_holdout374.csv`](../../final_dataset/valid_holdout374.csv).

- Preserved development manifest SHA-256:
  `39081fff8b1ec9a76606de9d43d59c4a31a90867e4da73a084beaf9fe902b18f`.
- Frozen question-spec SHA-256:
  `eadea93c7e0bb7f2cfaed411dbe58f74999c16cdf3f10747227b16f286897e44`.
- Frozen holdout CSV SHA-256:
  `22e914e410ab2cc8eb0c1df2bf2286f42a88e86683117263d7cc0f17a7b402b6`.

The question-only `micro-dispatch-v1` rule selects 44 questions. A mechanical
pre-answer audit found exact agreement with the pre-existing
`microbiologyevents` source stratum: 44/44, with zero dispatch-only or
label-only rows. The remaining 330 questions are the negative-control stratum.

## Arms and invariants

All 374 questions are answered once in each arm, interleaved by question with
rotating arm order. Model, effort, assumption block, answer schema, packet
renderer, Medplum instance, and grading are held fixed.

1. **A6a-r:** common repaired qo-v2.1 question-only planner (Amendment 1);
   200 root resources / 160,000 root evidence characters.
2. **QT-4V:** A6a-r plus `micro-vocab` exactly as frozen in
   [`QT_ARMS.md`](QT_ARMS.md). Outside dispatched questions the packet and
   rendered prompt must be byte-identical to A6a-r.
3. **QT-4T:** QT-4V plus `micro-traversal-v1` exactly as frozen in
   [`QT_ARMS.md`](QT_ARMS.md): allowlisted forward FHIR references only, depth
   2, at most 24 unique target attempts, 24,000 added evidence bytes, 48 path
   receipts, and 12,000 receipt bytes. Outside dispatched questions the packet
   and rendered prompt must be byte-identical to both other arms.

Before model execution, the zero-model gate must cover exactly 374/44/330
questions, prove the 330 negative-control packet and prompt identities, validate
all fetch and traversal receipts, verify frozen row metadata, and report gold
recall/mechanism changes. A failed gate blocks the run.

## Ordered hypotheses and decision rules

The family uses fixed-sequence gatekeeping at two-sided alpha .05.

### H1 — vocabulary effect

QT-4V correctness exceeds A6a-r correctness in the 44 pre-treatment dispatched
questions. Test: exact paired McNemar. Uncertainty: patient-cluster bootstrap
95% CI on the accuracy difference.

Vocabulary is promoted only if all are true:

- the dispatched-stratum point estimate is favorable and McNemar p<.05;
- the patient-cluster interval excludes zero;
- pooled 374-question accuracy is no more than 1 percentage point below A6a-r;
- no negative-control degradation greater than 1 point is observed; and
- the zero-model gate shows a positive gold-recall change.

### H2 — incremental traversal effect

Tested only if H1 passes. QT-4T correctness exceeds QT-4V correctness in the 44
dispatched questions. Test and uncertainty are identical to H1.

Traversal is accuracy-promoted only if all are true:

- the dispatched-stratum point estimate is favorable and McNemar p<.05;
- the patient-cluster interval excludes zero;
- pooled accuracy is no more than 1 point below QT-4V;
- no negative-control degradation greater than 1 point is observed; and
- traversal gains at least one gold-resource occurrence without losing one.

If H1 passes but H2 does not, vocabulary is promoted and traversal is not
claimed as an accuracy improvement. Its deterministic path-citation machinery
may still be evaluated separately as product infrastructure under byte
equivalence, authorization, latency, and correction/deletion tests.

## Grading and analysis

- Answer model: `gpt-5.6-sol`, `high` reasoning effort.
- One accepted answer per arm/question. Up to three operational attempts only
  under the already-frozen retry classifier; every failed attempt is retained
  and charged to all-attempt economics.
- Deterministic grading first; remaining items use the frozen arm-blind
  three-vote panel with the same model and effort.
- Primary inference is paired within question and clustered by patient for
  uncertainty.
- Report dispatched, negative-control, and pooled accuracy; abstention;
  deterministic versus panel routing; gold recall; all traversal statuses and
  path families; packet resources/bytes; accepted and all-attempt answer
  tokens; panel tokens if available; and wall time/monetary cost only if
  directly measured.
- No post-result retry, selector change, vocabulary change, bound change, or
  question exclusion is permitted. Any such change creates a new exploratory
  experiment.

## Licensed claims

Passing H1 licenses: "Fixed question-only microbiology vocabulary improved
answer correctness on an untouched FHIR-AgentBench holdout under the registered
model and harness."

Passing H2 additionally licenses: "Bounded exact-reference traversal improved
correctness beyond vocabulary alone on that holdout."

Neither result licenses claims that persistent graph storage caused the gain,
that arbitrary multi-hop clinical questions improve, or that results
generalize beyond this dataset/model. Those require the A11 path-required
benchmark and the storage byte-equivalence/latency benchmark.

## Amendment 1 — common-planner query-validity repair (pre-answer)

The first A6a packet-build attempt failed closed before writing a JSONL or
manifest and before any model call. A redacted diagnostic identified an HTTP
400 from qo-v2 emitting `_sort=date` on `Condition`, a resource type for which
this Medplum instance does not support the registered date sort. No answer or
gold content was inspected.

The common planner for all three arms is therefore the already-versioned
`qo-v2.1` repair built from the earlier 409-question failure audit, before this
holdout was opened. It applies the same token-boundary routing fixes and emits
first/last sorts only for resource types with a registered date search
parameter. Bounds, microbiology dispatch, fixed vocabulary, traversal
allowlist, traversal bounds, renderer, model, and grading do not change.

This repair is applied byte-identically before the treatment toggle in A6a-r,
QT-4V, and QT-4T, so the registered feature contrasts remain vocabulary versus
no vocabulary and traversal versus no traversal. The baseline is reported as
**A6a-r (qo-v2.1)** rather than silently conflated with the exploratory qo-v2
artifact. The repaired packet-builder SHA-256 is
`4f52297315bc12c418f2942f8162383435f62fa51b4ce40b09d9b4f76d58b218`.

If any frozen holdout query still produces an HTTP or incomplete-pagination
failure, packet construction fails again; the error is not converted into
clinical evidence and the run does not start.
