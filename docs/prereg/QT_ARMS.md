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

## Amendment 2026-07-13 — integrity gate and microbiology-first screen

This amendment was frozen before any QT-4 answer was generated or graded. It
does not change `micro-dispatch-v1`, `micro-v1`, `micro-traversal-v1`, qo-v2,
or any packet bound. It adds the following integrity and spend controls:

1. Before model execution, a file-only gate must verify all 409 packet rows,
   exact dispatch coverage, literal packet and rendered-prompt equality for all
   367 non-dispatched questions, gold-resource recall, packet bytes, traversal
   statuses, and path-family use. A failed gate blocks the answer run.
2. Packet-mode Codex runs execute from an empty isolated working directory.
   Any command/tool event in the structured event log quarantines that attempt
   as a harness failure. Packet bookkeeping fields, including the packet SHA,
   are removed before rendering; they remain in the frozen artifacts and
   manifests. This renderer correction is applied identically to all three
   same-run arms.
3. The first answer-bearing screen is restricted to the 42 pre-existing
   `microbiologyevents` questions, frozen in `qt4_micro42_spec.json`. Their
   order is a deterministic hash order, and arm order rotates A6a → QT-4V →
   QT-4T, then V → T → A, then T → A → V. The answering model remains pinned
   to `gpt-5.6-sol` at `high` effort. This screen estimates the registered
   vocabulary and traversal contrasts in the primary mechanism stratum while
   spending 126 answers rather than 1,227.
4. The 367-question negative-control stratum is mechanical in this screen:
   packet and prompt identity are established by the zero-model gate, but those
   questions are not re-answered. Therefore this screen cannot satisfy the
   pooled-accuracy promotion condition by itself. A favorable mechanism result
   may advance only to the already-registered full-set or untouched-holdout
   confirmation, where pooled degradation is measured.
5. The answering renderer exposes clinical resources plus fetched or
   already-present reference-path citations only. Search plans, treatment
   labels, packet kind/planner/count/bounds, source-ID ledgers, traversal
   version/limits/status counts, missing or capped edges, and fetch receipts
   remain artifact-only. This common renderer correction was frozen before any
   QT-4 answer; it prevents the words `micro-v1` and bookkeeping counts from
   cueing the model independently of clinical evidence.
6. Every root query in the 42-question answer-bearing microbiology stratum must
   carry a successful structured fetch receipt. Those 42 rows are rebuilt from
   the frozen qo-v2 selector; the 367 legacy negative controls are not answered
   and are used only for literal packet/prompt no-op checks. HTTP failure,
   incomplete pagination, an `OperationOutcome` in evidence, missing receipt,
   or inconsistent fetch-count arithmetic fails packet construction or the
   stratum gate. A bounded query may stop once its pre-registered per-query cap
   is reached; reaching an arbitrary backend page ceiling does not count as
   success.
7. Before importing experiment code, the live controller takes the singleton
   lock, copies the runner, harness, packet gate, and lock module into an
   immutable hash-verified bootstrap, and re-executes that staged runner while
   retaining the same lock. It then snapshots the exact input, gate, schema,
   harness, code, and three packet JSONLs under the locked run bundle. Its
   manifest is immutable; resume requires an audited per-question completion
   receipt bound to that manifest and the prompt/answer/event/packet hashes.
   Contamination, stale output, aliased arm directories, runtime drift, and
   partial capped runs cannot count as completed pairs.
8. Panel grading uses one three-arm queue with deterministic opaque item IDs and
   interleaved arms. The judge never receives an arm name or benchmark question
   ID. The registered panel is exactly three votes per item, batches of 20,
   `gpt-5.6-sol` at `high` effort, a 600-second process timeout, and the same
   controller-pinned Codex binary path and version as answering. Its registered
   configuration is `panel-cache-v2`, `panel-judge-v2`,
   `opaque-content-config-v1`, and `opaque-round-robin-v1`; the judge preamble
   SHA-256 is
   `abed333c02251ef78ec0d35b79207ed5d1ba6e55e8b26355c90b0bdf952b319c`
   and output-schema SHA-256 is
   `1d23dbf29dcea0e66f917fc7f7a32b43e316477c3998fc802f3e8b842c78a63c`.
   Vote caches are content-addressed over the judged text and this entire
   configuration; legacy or mismatched caches are invalid rather than reusable.
9. The current qo-v2 microbiology selector produces Observation roots, so this
   screen can directly exercise forward `Observation.hasMember` and
   `Observation.specimen` traversal. Registered DiagnosticReport paths remain
   measured, but zero use cannot support a claim about them. DiagnosticReport
   root selection or reverse traversal requires a separately versioned arm.
10. A stratum rebuild may replace only the nested clinical `packet`; it may not
    replace any top-level benchmark-row field. The zero-model gate retains the
    exact frozen CSV row and independently renders the live harness merge
    `{**input_row, **packet_record}`. It blocks execution if `question_id`,
    `question`, `question_with_context`, `patient_fhir_id`, or `assumption`
    differs from the frozen input. Negative-control prompt identity is measured
    from this same input-overlay rendering, not from packet JSONL rows alone.
11. Operational retries are capped at three attempts per arm/question. Every
    failed non-contaminated attempt is retained in an append-only ledger with
    its prompt, event log, return code, audit, usage, and hashes; only a clean
    accepted answer receives canonical `completion.json`. Contamination,
    orphaned output, stale prompts, and cross-controller receipts remain hard
    failures rather than retryable events.
12. Packet-byte economics use the same indented, sorted JSON renderer embedded
    in the answering prompt and are reverified against accepted `prompt.txt`.
    Final analysis must also carry forward the sealed gate's gold recall,
    traversal outcomes, and packet-resource counts before assessing whether an
    arm is merely a confirmation candidate. The 42-question screen cannot by
    itself satisfy the pooled-accuracy promotion condition.
13. The first capped controller smoke on 2026-07-13 reached the Codex CLI once
    but was rejected by the provider usage limit before an answer or token-usage
    receipt (`turn.failed`, no `turn.completed`, zero tool findings). It exposed
    a controller bug that classified every incomplete event stream as permanent
    tool contamination. That controller and its output directories are retained
    unchanged as an abandoned pre-answer artifact and are excluded from QT-4
    analysis. A fresh controller may treat an incomplete stream as an
    operational retry only for the exact four-event sequence observed here:
    one each of `thread.started`, `turn.started`, `error`, and `turn.failed`, in
    newline-terminated, strict UTF-8 JSONL with no duplicate object keys, the
    observed exact key shapes, a nonempty matching error message, and no
    item/tool event, completed turn, or canonical answer, plus a nonzero exit.
    Its full event log and marker
    remain in the append-only attempt archive. Any other incomplete, malformed,
    empty, tool-bearing, or merely truncated stream remains a permanent hard failure.
    The three-attempt cap is unchanged. No further call is authorized before
    the reported quota reset at 2026-07-19 23:05 PT.

The query-validity and question-routing repairs discovered in the run-2
failure audit are deliberately excluded from QT-4. They change the frozen
qo-v2 root selector and will be evaluated as a separately versioned planner
arm.

## Run plan

Packets: built locally per arm from the frozen base (SHA manifests). The
completed run-2 queue is no longer active. Per the 2026-07-13 amendment, the
42-question screen runs all three arms in rotating, question-level interleaved
order with `--skip-existing` resumability. Grading uses the identical pipeline
(det-v2 + 3-vote panel, arm-blind), assembled once across A6a/QT-4V/QT-4T so
the registered V-minus-A and T-minus-V contrasts share one frozen label per
arm/question. Token and actual model-visible packet-byte economics are reported
from accepted completion receipts only. An arm's packets differ from A6a's ONLY
in its feature's footprint — verified by diffing packet hashes and rendered
prompts wherever the feature is a no-op.
