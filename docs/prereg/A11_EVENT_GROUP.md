# A11 pre-registration — path-required event-group compilation

Status: **pre-answer; zero model calls**

Protocol and fixture version: `a11-event-group-v1`

Promoted baseline recipe: `qt4-vocabulary-promoted-v1`

Mechanism fixture: `fixtures/a11_event_group_cases.json`

## Question

On questions whose terminal evidence is absent from the promoted vocabulary
star packet, do bounded explicit-reference traversal and deterministic event
grouping improve answer correctness? In particular, does event grouping fix
the temporal-binding failure observed in QT-4 when flat traversal retrieved
the evidence but an older root was selected for a “last” question?

This tests a logical context projection, not a graph database. No result from
this protocol distinguishes Postgres, a materialized edge table, or a native
graph store unless those backends first produce byte- and authorization-
equivalent packets in a separate engineering benchmark.

## Efficacy arms and synthetic mechanism proxies

All arms start from the QT-4 holdout-promoted fixed vocabulary recipe.

The committed zero-model fixture does not run `compile_evidence.py`; it uses
hand-sealed synthetic resources and seed roots to exercise the compiler state
machine. Its artifact arm names therefore say `*_proxy`. Before efficacy, a
deterministic adapter must consume sealed `compile_evidence.py` records, prove
V packet byte equality, and bind both packet and adapter hashes. Until that
gate passes, the synthetic V/T/E proxies do not establish product-packet
equivalence.

### V — promoted vocabulary star

Patient-scoped root selection using `qt4-vocabulary-promoted-v1`. Do not
follow outbound references. This is the product baseline, not the retired
query-blind projection.

### T — promoted vocabulary plus flat traversal

Follow only the registered relative FHIR references, within the frozen type,
relation, depth, target, edge, path-citation, byte, practice, purpose, patient,
and exact-version bounds. The only allowed relations are
`Observation.hasMember -> Observation`,
`Observation.specimen -> Specimen`,
`DiagnosticReport.result -> Observation`, and
`DiagnosticReport.specimen -> Specimen`; exact JSON-pointer shape is required,
so nested references cannot masquerade as a registered edge. Append the
retrieved resources and replayable JSON-pointer paths flat. This is the same
shape that exposed QT-4's temporal-binding failure.

### E — promoted vocabulary plus event-group compilation

Use the exact T retrieval result. Replace the flat evidence list with:

- one group per registered root event;
- compact typed node views;
- typed edges with replayable JSON pointers;
- a canonical clinical event timestamp and the exact FHIR field supplying it;
- deterministic first/latest rank with ID tie-breaking;
- an explicit selected-event marker derived only from the question's temporal
  operator; and
- a deterministic answerability receipt over registered path shapes.

Before gold or audit fields are consulted, `plan_question(question)` deterministically
derives and seals the family, temporal policy, path signatures, and question
hash. E may consume only that question plan plus T's exact retrieval result.
Counterfactual changes to reference answers, expected roots, expected
evidence, answerability labels, or audit-only path metadata must not change
the model packet.

The receipt may state whether the selected group contains a planned path. It
must not contain reference answers, gold resource IDs, benchmark labels,
unavailable target IDs, exact stale versions, or the reason a target is
unavailable. T applies the same model-safe redaction. Clinical rank uses only
registered resource-specific `effective[x]` fields; `issued`,
`meta.lastUpdated`, and other publication/store timestamps may not rank
events.

Every seed must explicitly name the requested patient. Same-practice seeds
for another patient and patient-ambiguous seeds fail closed before traversal.
Requested and resolved references are carried separately for available exact
versions. Each canonical target is charged to the target budget once, while
additional replayable paths do not consume another target. Discovery stops at
128 edges and path materialization at 256 citations; either limit yields a
generic insufficient E receipt and an outer audit outcome. V, T, and E share
one post-projection byte gate.

Arm labels, internal limits, unavailable target IDs/reasons, and gold audit
fields remain outside the answer prompt. The V/T/E prompt wrapper, answer
schema, model, effort, retry policy, and grading are otherwise fixed.

## Zero-model mechanism gate

Before constructing an efficacy dataset:

1. V lacks every registered terminal evidence resource.
2. T and E contain every answerable terminal through a path of at least two
   explicit references.
3. Every E edge replays against the sealed source JSON pointer.
4. E chooses the correct first/latest root under deterministic ordering.
5. A missing terminal on the selected latest event yields `insufficient` even
   if an older event is complete.
6. The answerability receipt contains path requirements, not answers or gold.
7. Scope leakage is zero in every arm.
8. Cross-practice, cross-patient, purpose-denied, and stale-version targets
   remain opaque in every model packet.
9. Available exact-version paths retain separate requested and resolved
   references and replay correctly.
10. Every arm fails closed at its packet byte boundary, duplicate/convergent
   paths do not consume extra target slots, and cycles terminate.
11. Two builds from identical inputs produce byte-identical artifacts and
   manifests.

The committed ten-case synthetic fixture exercises two-hop
`Observation.hasMember -> Observation.hasMember`, two-hop
`Observation.hasMember -> Observation.specimen`, first and latest ranking,
latest-event missing-terminal safety, cross-practice, cross-patient seed and
terminal denial, purpose-denial, stale-version, and available exact-version behavior. It
contains no PHI and makes no model calls. Passing it proves only compiler
mechanics.

## Efficacy dataset to seal before answers

Build at least 120 non-PHI questions by a committed deterministic extractor,
not hand-picking favorable rows. Freeze the extraction algorithm before
reading answer outputs. Requirements:

- at least four registered path families;
- depths two and three;
- at least 60 first/latest questions;
- at least 20% unanswerable cases, including missing, stale-version,
  out-of-scope, and bound-exhaustion conditions;
- patient-disjoint development and efficacy partitions;
- deterministic hash-order selection within family;
- mechanically derived reference answers and terminal resource IDs;
- duplicate, label-leakage, and star-answerability audits; and
- one sealed manifest binding input snapshot, question order, packet hashes,
  compiler hashes, model, Codex version, prompt, answer schema, panel, and
  grading configuration.

No efficacy question is eligible unless V lacks its terminal evidence, T and
E contain it through the registered path, and the answer is not recoverable
from root labels or question metadata. Answer content is not inspected
mid-run.

No efficacy dataset is eligible until the product-packet adapter proves that
V is the exact promoted-recipe packet, T and E use the exact same retrieved
source receipt including authorized roots, and none of the adapter inputs are
hand-authored from answer or gold fields.

The 2026-07-14 path-bound adapter implementation now verifies a strict product
JSONL/manifest envelope against an independently supplied manifest SHA-256,
recomputes the promoted packet hash, rejects forbidden benchmark metadata and
cross-patient roots, and returns the exact
`codex_harness.render_model_visible_packet` bytes for V. This closes the V
rendering ambiguity. Strict metadata schemas do not prove that arbitrary FHIR
clinical fields contain no answer aliases, so the efficacy seal must also bind
the source corpus and deterministic extractor. The adapter does not synthesize
authorization: the efficacy seal must still provide a governed
principal/practice/purpose/patient/source-version receipt and one immutable
version-preserving traversal result shared by T and E. See
[`A11_PRODUCT_PACKET_ADAPTER.md`](../results/A11_PRODUCT_PACKET_ADAPTER.md).

The 2026-07-14 sealed QT-4 inventory does not satisfy these requirements: only
ten rows have a fetched depth-two target, and only one two-hop family is
present. Therefore the 120-question efficacy run is blocked on a new or
extended non-PHI substrate. The inventory result is frozen separately in
[`A11_CANDIDATE_INVENTORY.md`](../results/A11_CANDIDATE_INVENTORY.md). A
smaller run on the current rows must be labeled a mechanism/debugging pilot,
not efficacy confirmation.

A subsequent local audit found a large synthetic HolyFHIR export with many
generic graph paths but zero populated depth-two paths under this protocol's
four registered microbiology families and only 11 patient clusters. It does
not unblock efficacy. The pinned-source and deterministic-augmentation options
are recorded in [`A11_SUBSTRATE_AUDIT.md`](../results/A11_SUBSTRATE_AUDIT.md).

The same 2026-07-14 producer-feasibility audit found a second hard blocker:
`qo-v2.1` maps microbiology questions to `Observation` queries and has no
`DiagnosticReport` query path. Therefore exact promoted V packets cannot
currently supply roots for the two registered DiagnosticReport families. No
efficacy run may begin until a dated pre-answer amendment either adds and
versions that product query behavior or changes the family requirement, then
re-seals the adapter and dataset without consulting answer outputs.

## Outcomes and fixed analysis order

### Primary: E minus T

Paired answer correctness on the registered temporal/path-required stratum.
Report counts, percentage-point difference, patient-cluster bootstrap 95%
interval, and exact McNemar test. Promotion requires a positive estimate, an
interval excluding zero, and zero critical safety failures.

### Registered secondary: T minus V

Paired answer correctness on all path-required answerable questions, with the
same uncertainty report. This asks whether the new topology finally gives
traversal a setting in which it earns its complexity.

Also report terminal-evidence recall, selected-root accuracy, date-order
errors, abstentions, unsupported answers, path validity, answerability
calibration, packet bytes, compilation latency, accepted/all-attempt tokens,
retry yield, and results by path family and depth.

E may cost more tokens than T. The mechanism fixture already shows that typed
structure has overhead; no efficiency claim is licensed unless the measured
efficacy run offsets it through fewer answer tokens, retries, or errors.

## Hard failures

The experiment fails regardless of accuracy if V is not byte-identical to its
sealed promoted-recipe source, any packet crosses patient, practice, or
purpose scope, an unregistered/nested edge traverses, an exact version resolves
incorrectly, a path cannot replay, store/publication time is used as clinical
time, selected-event rank is nondeterministic, the answerability receipt leaks
gold or arm/bound labels, an arm exceeds a registered bound, T and E retrieval
receipts differ, or accepted/all-attempt token receipts cannot be reconciled.

## Zero-model commands

```bash
python a11_event_group_benchmark.py \
  --fixture fixtures/a11_event_group_cases.json \
  --output-dir runs/a11-event-group-v1

python -m pytest -q \
  tests/test_a11_candidate_inventory.py \
  tests/test_promoted_evidence_recipe.py \
  tests/test_a11_packet_adapter.py \
  tests/test_a11_event_group_benchmark.py
```
