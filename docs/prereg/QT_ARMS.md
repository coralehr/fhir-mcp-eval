# Query-time single-feature arms (QT-1..4) — mini pre-registrations

*2026-07-12. Successors to the killed A6g bundle, per the chart-graph
adversarial review (cstack `plans/reviews/2026-07-12-chartgraph-adverse.md`,
findings 9–11) and the reworked ROADMAP entry. Base for every arm: the frozen
A6a configuration (planner qo-v2, bounds 200/160k, same substrate/instance/
prompt/schema — including the run-2 assumption block). Each arm toggles
EXACTLY ONE feature via `--features`.*

## Status labels (binding)

**Every result on the 409 test set is EXPLORATORY by definition** — these
mechanisms were chosen by inspecting that set's failures (winner's-curse
rule). Confirmatory promotion of any arm requires an untouched holdout: the
unused valid-split questions (374 remaining) or a second benchmark.
Comparisons are paired vs the same-substrate A6a baseline; report McNemar +
cluster CI, no stars on 409 results.

## QT-1 `include-pinning`

- **Treatment:** Medication/Location resources referenced by kept resources
  are never independently evicted (reference-closure pass after bounding,
  exempt from caps; count recorded per packet).
- **Prediction:** prescriptions stratum improves (measured failure class:
  3 forced abstentions + wrong-name answers from evicted Medication
  displays); all other strata unchanged.
- **Falsifier:** prescriptions stratum flat → feature dropped (code
  reverted, not kept as a detail).

## QT-2 `agg-summary`

- **Treatment:** a question-blind deterministic reducer block computed over
  the FULL fetched set pre-bounding rides in every packet: per-type counts,
  per-(type, code) resource counts with first/last dates, unit-guarded
  min/max/sum, distinct-medication-display counts. Semantics stated in the
  block itself (finding 3's ambiguity objection answered by making the
  semantics explicit and versioned rather than pretending "count" is
  self-defining).
- **Prediction:** aggregation-phrased questions improve (measured class:
  7+ correct-but-forced abstentions where gold COUNT/SUM exceeded retained
  rows; outputevents totals; prescription counts). Single-value lookup
  strata unchanged.
- **Falsifier:** aggregation subset flat → dropped.

## QT-3 `endpoint-reserve`

- **Treatment:** both temporal extremes of every resource type are packed
  before general round-robin, so one noisy type's char pressure cannot
  evict another type's endpoint.
- **Prediction:** first-vs-last comparison questions improve (measured
  class: 3 dropped-endpoint failures); otherwise unchanged.
- **Falsifier:** flat → dropped.

## QT-4 `micro-traversal` (NOT YET BUILT)

- **Treatment (planned):** micro-aware vocabulary (culture/screen/smear/
  gram stain/organism display list) + bounded `hasMember`/`Specimen`
  traversal at fetch time.
- **Prediction:** microbiology stratum moves from 14.3%; everything else
  unchanged.
- Requires fetch-by-id infrastructure; built after QT-1..3 read out.

## Amendment 2026-07-12 — split QT-4V vocabulary from QT-4T traversal

This dated amendment preserves the original QT-4 text above as the historical
proposal. It was frozen before any QT-4 packet answering or grading. The
original proposal changed two mechanisms at once, so it is replaced by the
following ordered contrasts. Results on the 409-question test set remain
exploratory under the status rule at the top of this file. This amendment does
not alter QT-3 or the already-frozen generality grid, and it authorizes local
packet construction only—not an answer run while the existing queue is active.

All registered question-only arms use the frozen A6a root-packet constants
`A6A_MAX_TOTAL_RESOURCES=200` and `A6A_MAX_PACKET_CHARS=160000`. CLI defaults,
programmatic packet builds, and manifests enforce those exact values before
live fetching; an override is an invalid arm, not a tuning parameter.

### Frozen dispatch and vocabulary

The `micro-dispatch-v1` treatment dispatcher reads question text only. A
question enters the microbiology path when its lower-cased text contains one
of: `microbiolog`, `microbial`, `culture`, `specimen`, `organism`, `smear`,
`gram stain`, or `screen`. The
pre-existing `main_table_name=microbiologyevents` label is used only for
stratified analysis; the planner never reads it. A pre-freeze mechanical audit
matched all 42/42 microbiology questions and 0/367 non-microbiology questions;
that audit inspected dispatch only, not answers or QT-4 outputs.

**QT-4V `micro-vocab`** keeps A6a qo-v2, its date/patient filters, temporal
sorts, bounds, renderer, prompt, and answering substrate fixed. For dispatched
Observation queries only, the free-text `code:text` term is replaced by four
fixed display-token queries, in this order: `culture`, `gram stain`, `screen`,
`smear` (`micro-v1`). This four-query union is complete: an empty fixed-token
query is not relaxed to a bare Observation search, which would reintroduce the
measured routine-lab drowning failure. There is no reference traversal.
Outside the dispatched questions QT-4V stores the literal A6a packet
(including a truthful packet SHA); arm identity remains in the manifest. The
resulting model prompt is therefore byte-identical to A6a.

### Frozen traversal contract

**QT-4T `micro-vocab,micro-traversal`** uses the byte-identical QT-4V search
plan and adds only a post-bounding exact-reference traversal
(`micro-traversal-v1`). Roots are kept QT-4V Observation or DiagnosticReport
resources. The complete allowlist is:

- `Observation.hasMember` → `Observation`
- `Observation.specimen` → `Specimen`
- `DiagnosticReport.result` → `Observation`
- `DiagnosticReport.specimen` → `Specimen`

Only relative `ResourceType/id` references are eligible, and IDs must match the
FHIR R4 id grammar `[A-Za-z0-9\-.]{1,64}` before an `_id` query is issued.
There is no reverse search, inferred edge, terminology expansion, generic
recursion, or traversal from an evicted root. Traversal order is source ID,
JSON path, then target ID. Targets are fetched by exact ID and projected with
the same A6a field-removal rules.

Hard traversal bounds, separate from the frozen A6a root-packet bounds:

- maximum depth: **2** reference hops;
- maximum unique target fetch attempts: **24** (missing targets consume a
  slot);
- maximum added serialized evidence: **24,000 UTF-8 bytes**, measured over
  deterministic compact JSON after projection;
- maximum serialized path receipts: **48 receipts** and **12,000 UTF-8
  bytes**, measured over the deterministic compact-JSON receipt array.

Cycles and duplicate targets add no duplicate resource. Eligible edges
considered within the depth bound emit deterministic path receipts, up to the
receipt bounds, with `depth`, `from`, `path`, `to`, and one of `fetched`,
`already_present`, `missing`, `max_resources`, or `max_serialized_bytes`.
Packet resources and source IDs remain deduplicated: QT-4V root order is
unchanged, traversed targets append in sorted ID order, and the source-ID
ledger is sorted. Beyond the receipt count/byte budget, edges are not
serialized and their count is recorded as `path_receipts_omitted`. Outside
dispatched questions QT-4T stores the literal A6a packet and must produce a
byte-identical model prompt to both QT-4V and A6a.

Before receipt serialization or truncation, the traversal records complete
deterministic counts for every receipt status. Thus fetched, already-present,
missing, resource-capped, and byte-capped edge analyses remain available even
when individual receipts exceed their own count or byte budget.

The only registered feature sets are the empty A6a base, each existing
single-feature QT-1/QT-2/QT-3 arm, QT-4V (`micro-vocab`), and QT-4T
(`micro-vocab,micro-traversal`). Both CLI and programmatic packet builders
reject feature mixtures and reject every QT feature unless the planner is
question-only qo-v2.

### Comparisons, outcomes, and promotion

The hierarchical comparisons are:

1. **Vocabulary effect:** QT-4V versus same-run A6a.
2. **Incremental traversal effect (primary QT-4T contrast):** QT-4T versus
   QT-4V. QT-4T versus A6a is descriptive only because it combines both
   mechanisms.

Primary analysis stratum: the pre-existing 42 `microbiologyevents` questions.
All other questions are a negative-control stratum and must satisfy the no-op
prompt invariant. Report paired correctness with patient-clustered intervals
and McNemar where estimable, plus gold-resource recall, fetched/missing/pruned
reference counts, packet resources, UTF-8 bytes, and answering-token usage.

An arm earns a confirmatory run only if its registered incremental contrast
has a favorable accuracy point estimate in the microbiology stratum, no pooled
accuracy degradation greater than 1 percentage point, and its mechanism
actually changes the expected retrieval/path metric. Promotion means freezing
code, question IDs, packet JSONL, and SHA manifest before answering the
untouched remaining valid-split questions (or a second benchmark). A null or
adverse incremental contrast is dropped rather than retained as an
implementation detail. No QT-4 result licenses a claim about persistent graph
storage; storage is a separate zero-model byte-equivalence benchmark.

## Run plan

Packets: built locally per arm from the frozen base (SHA manifests). Answer
runs: queued on the shared Codex substrate BEHIND run 2; one arm per quota
window, `--skip-existing` resumable. Grading: identical pipeline (det-v2 +
3-vote panel, arm-blind). An arm's packets differ from A6a's ONLY in its
feature's footprint — verified by diffing packet hashes on questions where
the feature is a no-op.
