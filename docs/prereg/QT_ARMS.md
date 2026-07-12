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

## Run plan

Packets: built locally per arm from the frozen base (SHA manifests). Answer
runs: queued on the shared Codex substrate BEHIND run 2; one arm per quota
window, `--skip-existing` resumable. Grading: identical pipeline (det-v2 +
3-vote panel, arm-blind). An arm's packets differ from A6a's ONLY in its
feature's footprint — verified by diffing packet hashes on questions where
the feature is a no-op.
