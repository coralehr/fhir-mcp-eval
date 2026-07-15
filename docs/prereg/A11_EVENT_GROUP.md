# A11 pre-registration — path-required event-group compilation

Status: **pre-answer gates sealed; zero answer or judge calls**

Protocol and fixture version: `a11-event-group-v1`

Promoted baseline recipe: `qt4-vocabulary-promoted-v1`

A11 producer recipe after the 2026-07-14 pre-answer amendment:
`a11-four-family-v1`

Mechanism fixture: `fixtures/a11_event_group_cases.json`

## Dated pre-answer controller-integrity amendment — 2026-07-15

This amendment was committed before any A11 answer or judge call. It does not
change an arm, question, contrast, promotion threshold, or analysis stratum.
It closes implementation ambiguities found during independent controller
review:

- the exact dataset, answer-input, prompt, schema, runtime, controller, panel,
  grading and preregistration bytes are bound before execution and replayed or
  snapshotted into one immutable controller;
- answer inputs are independently rematerialized from the registered dataset
  and must compare byte-for-byte before sealing;
- only the exact, well-formed, answerless provider-failure event shape may be
  retried; a completed malformed/model-invalid output hard-stops the run and
  is never resampled;
- substantive responses to registered unanswerable questions are
  deterministically incorrect, while the arm-blind panel judges only the
  registered categorical aliases for answerable questions; and
- accepted and all-attempt token receipts report missing-receipt counts and
  completeness explicitly. Unreconciled economics remains a hard failure.

Registered `retry yield` is questions recovered after at least one exact
provider-failure retry divided by retry attempts, reported per arm (with both
counts retained). A registered `unsupported answer` is a substantive answer
to an unanswerable item, a substantive answer with no cited source id, or any
cited source id absent from that arm's model-visible packet. Path validity is
the sealed dataset audit's exact JSON-pointer replay against source resources;
the final result also reports model-packet path structure separately. Panel
majorities and judge-token economics are re-derived from the complete expected
vote/batch attempt inventory, and unknown attempt receipts hard-fail audit.

The governance/identity boundary is also clarified before answers: principal,
practice, purpose, policy and source-version identifiers stay outside model
packets. Opaque synthetic FHIR resource ids and references, including Patient
references, are model-visible in every arm to preserve graph topology. They
contain no PHI and may not encode arm or answer labels.

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

## Dated pre-answer producer amendment — 2026-07-14

This amendment was committed before any A11 answer or judge call. The
producer-feasibility audit showed that the historical `qo-v2.1` planner could
never emit `DiagnosticReport` roots, making two of the four registered path
families structurally unreachable. Removing those families after seeing that
failure would weaken the topology test, while silently changing the promoted
recipe would invalidate QT-4 reproduction. The protocol therefore adds a new,
explicit recipe rather than reinterpreting the old one.

`a11-four-family-v1` freezes:

- question-only planner `qo-v2.2-a11-four-family`;
- the exact QT-4-promoted `micro-vocab` terms, dispatcher, resource and byte
  bounds, serializer, and no-relaxation policy;
- `Observation` roots unless the question explicitly says `diagnostic report`
  or `DiagnosticReport`; and
- `DiagnosticReport` roots only under that explicit question wording.

Because the FHIR `DiagnosticReport` `date` search parameter may include
`issued`, the A11 recipe does not use server-side `date` sorting or date
windows for DiagnosticReport questions. It fetches the bounded vocabulary
union, then orders and bounds roots locally from `effectiveDateTime` or
`effectivePeriod.end/start` only. A root without `effective[x]` fails packet
construction; `issued`, `meta.lastUpdated`, and generic `date` never substitute.

Every eligible v2 template must also contain at least one frozen microbiology
dispatcher term (`microbiolog*`, `microbial`, `culture`, `specimen`,
`organism`, `smear`, `gram stain`, or `screen`). Generic `finding` wording by
itself is rejected rather than creating a question plan whose producer packet
would not receive the vocabulary treatment.

The separately versioned `a11-question-plan-v2-four-family` maps question text
to exactly these registered shapes:

| Explicit root wording | Terminal wording | Registered path |
|---|---|---|
| Observation/default | organism/finding/gram stain | `Observation.hasMember -> Observation.hasMember` |
| Observation/default | specimen | `Observation.hasMember -> Observation.specimen` |
| DiagnosticReport | organism/finding/gram stain | `DiagnosticReport.result -> Observation.hasMember` |
| DiagnosticReport | specimen | `DiagnosticReport.result -> Observation.specimen` |

The historical `qt4-vocabulary-promoted-v1`, `qo-v2.1`, and
`a11-question-plan-v1` paths remain unchanged and are covered by regression
tests. The 2026-07-15 amendment below supersedes the recipe binding for the
depth-balanced efficacy corpus; the v2 recipe remains the frozen two-edge
producer gate. V is still the no-traversal
packet; T and E may only add the registered governed retrieval. This amendment
is a reachability and protocol result, not evidence that DiagnosticReport
selection, traversal, event grouping, or a graph database improves accuracy.

## Dated pre-answer depth/source amendment — 2026-07-15

The four-family amendment made all registered two-edge families reachable but
did not make the preregistered depth-three stratum executable: its question
planner emitted only two-edge signatures. Before any A11 answer or judge call,
this amendment adds the isolated `a11-four-family-depth-aware-v1` recipe. It
reuses the exact `qo-v2.2-a11-four-family` root producer and fixed
`micro-vocab`; it changes only the bound question-plan version to
`a11-question-plan-v3-depth-aware`.

Depth two uses the existing four signatures. Depth three must contain the
literal question-only phrase `through an intermediate observation` and inserts
one registered `Observation.hasMember` edge before the terminal relation:

| Family | Depth 2 | Depth 3 |
|---|---|---|
| Observation finding | `hasMember/hasMember` | `hasMember/hasMember/hasMember` |
| Observation specimen | `hasMember/specimen` | `hasMember/hasMember/specimen` |
| DiagnosticReport finding | `result/hasMember` | `result/hasMember/hasMember` |
| DiagnosticReport specimen | `result/specimen` | `result/hasMember/specimen` |

Other depth wording containing `intermediate`, `three hop`, `three-hop`, or
`3-hop` fails closed. The historical v1 and four-family v2 question planners,
and both earlier evidence recipes, are not reinterpreted.

Clinical root order is separately bound as
`a11-event-rank-v2-normalized-utc`: parse the registered `effective[x]` instant,
normalize it to a UTC instant, then tie-break by `ResourceType` and `id`.
Publication/store timestamps remain forbidden.

The efficacy source is the official
`synthetichealth/synthea-sample-data` archive at commit
`0d9dc0b56534cacb36db31c84e390ae936d03653`, generated from Synthea commit
`2b0a55bab0ab9ae22204320c80f5880ceb8925aa`. The pinned artifact is
`downloads/latest/synthea_sample_data_fhir_latest.zip`, Git blob
`e0d5f1f46a08bc0b373f7bc211b87dc2319572c9`, 44,578,263 bytes, SHA-256
`d32f10f98ec36bc6784bfe5f4e112d4850a6d0cb5dda6b9d8ca18fff5fb4a1d1`.
Its upstream generation seed is unknown, so the raw archive bytes, ordered ZIP
entry names, every entry hash/byte count, and selected JSON-content hash are
the reproducibility boundary. No upstream-seed claim is licensed.

The archive contains 115 unique Patients. Hash-order them before augmentation,
then freeze 15 development and 100 efficacy patients with no overlap. Generate
exactly 24 development questions (three in each of eight family-depth cells)
and 120 efficacy questions (15 per cell). In efficacy, exactly 80 patients have
one row and 20 have two; no patient crosses partitions. Efficacy contains 60
first and 60 latest questions and exactly 24 unanswerable questions, three per
cell and six each for missing target, stale version, out-of-scope target, and
target-bound exhaustion. Development remains non-confirmatory.

Generated identifiers, labels, and event times are opaque and may not encode
family, depth, split, answerability, failure mode, temporal policy, root, or
terminal role. Every augmented resource has `meta.versionId`, every registered
edge carries a replayable version-specific relative reference, and source,
question, gold, audit, policy, and eventual model-packet envelopes remain
separate. Each question binds the SHA-256 of a canonical policy context carrying
principal, practice, purpose, patient, allowed purposes, and logical source
epoch; every augmented resource has an explicit subject binding to that patient.
Eligibility requires exactly one route from selected root to terminal, an
exact shortest-path match to the registered family-depth cell, no direct or
alternate shorter route, no duplicate terminal route, no terminal ID/display/
code alias in V, and byte-identical rebuilds from the sealed inputs.

## Dated pre-answer temporal-identifiability correction — 2026-07-15

An independent read-only review before any answer or judge call found that the
first candidate builder left the nonselected temporal root incomplete. That
would let T answer from the only complete fact without resolving first versus
latest, so the candidate seal was superseded and never licensed for answers.

The corrected frozen generator gives both temporal roots distinct terminal
facts through the same registered family-depth path. In answerable rows both
paths are complete, so flat T contains two competing facts and E must bind the
question's temporal operator to the selected group. In every unanswerable row
the nonselected event remains complete while the selected event fails through
exactly one independently audited registered mechanism. Each failure mode is
balanced across temporal policy: three first and three latest rows per mode.
In particular, every latest-unanswerable row contains a complete older event.
A label-only failure-mode change must fail the audit.

The same pre-answer review required a one-pass immutable source snapshot,
pre/post compiler-dependency equality, path-stable logical input names, and
exclusive seal outputs. Those integrity repairs change only construction and
receipts, not the registered V/T/E treatments or analysis order.

## Efficacy arms and synthetic mechanism proxies

All efficacy arms start from `a11-four-family-depth-aware-v1`. It reuses the
same `a11-four-family-v1` root producer and QT-4-promoted fixed vocabulary,
while binding the depth-aware question plan required for the eight registered
family-depth cells.

The committed zero-model fixture does not run `compile_evidence.py`; it uses
hand-sealed synthetic resources and seed roots to exercise the compiler state
machine. Its artifact arm names therefore say `*_proxy`. Before efficacy, a
deterministic adapter must consume sealed `compile_evidence.py` records, prove
V packet byte equality, and bind both packet and adapter hashes. Until that
gate passes, the synthetic V/T/E proxies do not establish product-packet
equivalence.

### V — A11 four-family vocabulary star

Patient-scoped root selection using `a11-four-family-depth-aware-v1`. Do not follow
outbound references. For Observation-root questions this is the promoted
product behavior; for explicitly worded DiagnosticReport questions it is the
pre-answer versioned extension above. It is not the retired query-blind
projection.

### T — A11 four-family vocabulary plus flat traversal

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

### E — A11 four-family vocabulary plus event-group compilation

Use the exact T retrieval result. Replace the flat evidence list with:

- one group per registered root event;
- compact typed node views;
- typed edges with replayable JSON pointers;
- a canonical clinical event timestamp and the exact FHIR field supplying it;
- deterministic first/latest rank with ID tie-breaking;
- an explicit selected-event marker derived only from the question's temporal
  operator; and
- a deterministic answerability receipt over registered path shapes.

Before gold or audit fields are consulted, the recipe-bound adapter calls
`plan_question(question, version="a11-question-plan-v3-depth-aware")` for the
efficacy recipe and
deterministically seals the family, temporal policy, path signatures, and
question hash. Calling `plan_question(question)` without the version remains
the historical mechanism-fixture behavior and is ineligible for A11 efficacy.
E may consume only the v3 question plan plus T's exact retrieval result. The
v2 plan remains frozen only for the historical `a11-four-family-v1` producer
gate.
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

Every seed must explicitly name the requested patient. Patient references must
be one unambiguous relative or absolute `Patient/{id}` resource path; a path
containing multiple `Patient` segments fails closed. Same-practice seeds
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

Build exactly 120 efficacy and 24 development non-PHI questions by a committed deterministic extractor,
not hand-picking favorable rows. Freeze the extraction algorithm before
reading answer outputs. Requirements:

- exactly four registered path families and eight family-depth cells;
- depths two and three with the exact cell quotas above;
- exactly 60 first and 60 latest efficacy questions;
- exactly 20% unanswerable efficacy cases, including missing, stale-version,
  out-of-scope, and bound-exhaustion conditions;
- patient-disjoint development and efficacy partitions;
- global deterministic Patient hash ordering followed by the frozen
  family/depth assignment schedule;
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
authorization; the subsequent efficacy seal therefore provides a governed
principal/practice/purpose/patient/source-version receipt and one immutable
version-preserving traversal result shared by T and E. See
[`A11_PRODUCT_PACKET_ADAPTER.md`](../results/A11_PRODUCT_PACKET_ADAPTER.md).

The 2026-07-15 pre-answer governed-retrieval gate now closes that second
boundary for synthetic efficacy inputs. `a11-governed-retrieval-v1` consumes
only an adapter-verified V bundle, an independently pinned source snapshot and
an independently pinned canonical benchmark policy artifact; derives roots only from V; validates
`meta.versionId`; preserves requested versus resolved historical references;
and seals one immutable retrieval-source hash consumed unchanged by T and E.
Governance identifiers for principal, practice, purpose, policy and source
version are hashed in the outer receipt and never enter model-visible packets.
Opaque synthetic FHIR resource ids and references, including the Patient id,
do enter every arm because referential integrity is part of the graph task;
they contain no PHI and may not encode arm or answer labels. This is benchmark
governance, not proof of Bonfire's production ABAC. The multi-family dataset and its
deterministic producer/eligibility audit subsequently passed as described in
[`A11_DATASET_GATE.md`](../results/A11_DATASET_GATE.md). See
[`A11_GOVERNED_RETRIEVAL_GATE.md`](../results/A11_GOVERNED_RETRIEVAL_GATE.md).

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

The same 2026-07-14 producer-feasibility audit found that `qo-v2.1` could not
supply roots for the two registered DiagnosticReport families. The dated
amendment above resolves that reachability blocker without changing the
historical recipe. The actual `compile_evidence.py` entrypoint now emits both
root types under `a11-four-family-v1`, and adapter v2 re-derives and validates
the corresponding planner, manifest, query plan, packet bytes, and root type
on synthetic non-PHI integration records. No model was called. The 2026-07-15
amendment now implements the deterministic multi-family substrate builder; the
governed authorization/source-version receipt is also implemented and tested.
The parent-side double-build seal and complete
producer/adapter/governed-retrieval preflight passed without model calls on
2026-07-15. Both builds produced manifest SHA-256
`442ca8d204fbd81f06e0abaf2ea5022b375deabb71a93d5ebaeccef98e99fe3c`.
The answer runner remains blocked until a separate controller manifest binds
that dataset hash to the model, Codex version, prompts, answer schema, retry
policy, panel, grading configuration, and fixed analysis order required above.

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
  tests/test_a11_four_family_recipe.py \
  tests/test_a11_packet_adapter.py \
  tests/test_a11_event_group_benchmark.py
```
