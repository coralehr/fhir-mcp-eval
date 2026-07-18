# Experiment program forensic synthesis

Updated: 2026-07-17

## Bottom line

The program supports two product claims and one bounded mechanism claim:

1. Deterministic question-aware selection beat query-blind projection twice on
   the paired 409-question FHIR benchmark.
2. A fixed microbiology vocabulary improved the selected packet on an
   untouched valid-split dispatched stratum.
3. Bounded traversal recovered terminal evidence on A11's constructed
   path-required benchmark.

The evidence does **not** establish that event grouping improves accuracy,
that the effects generalize across model families or APIs, or that Bonfire
should use a native graph database. An explicitly unregistered A11b r3 preview
completed 1,152 answer slots across T0, T1, and E1. Its normalized artifacts
tied at 288/384, but raw-log review found that the compatibility normalizer
erased 219 structured insufficiency reasons. T1/E1 used one of two explicit
insufficiency prefixes on all 96 unsupported cases versus 26 for T0. This is a
large post-hoc signal warranting a fresh prospective T1 test, not retroactive
promotion. E1 still added no observable grouping benefit beyond T1. The used
efficacy Patients are now spent for confirmatory claims.

The canonical machine-readable record is
[`EXPERIMENT_EVIDENCE_LEDGER.json`](EXPERIMENT_EVIDENCE_LEDGER.json). Its
generated human view is
[`EXPERIMENT_EVIDENCE_LEDGER.md`](EXPERIMENT_EVIDENCE_LEDGER.md).

## Reliability by experiment family

| Evidence | Reliability judgment | What it licenses |
|---|---|---|
| A6a initial and repaired run 2 | Confirmatory paired result replicated after the shared time assumption was repaired. | Selection on this benchmark. |
| QT-1, QT-2, QT-3 | Valid null results. | Do not ship include pinning, summary generation, or endpoint reservation as accuracy improvements by themselves. |
| Exploratory six-cell generality grid | Invalid for claims: each cell records 99 answered questions but its archived aggregate divides by 409. | Nothing until deterministic regrading on the registered 99-question cells. |
| QT-4 micro42 | Mechanism screen only. | It justified untouched confirmation, not promotion by itself. |
| QT-4 valid374 | Confirmatory for the 44-question dispatched stratum with 330 byte-identical controls. | Fixed microbiology vocabulary; traversal remained unresolved. |
| A11 V/T/E | Strong constructed-task mechanism evidence, but E bundled event grouping with other aids. | Bounded path traversal, not an isolated event-grouping gain. |
| A11b | The strict normalized artifacts tie at 75.0%, but the normalizer erased T1/E1's raw insufficiency signal. Supported cases had no headroom and the semantic sensitivity is post-result. | Do not promote. Prospectively retest T1 on a fresh discriminating holdout; E1 grouping remains unsupported. |

## Adversarial findings

### What held up

- A6a's relative selection advantage survived a repaired prompt and pinned
  runtime: +8.31 percentage points, cluster 95% CI +3.77 to +12.90.
- QT-4's vocabulary gain survived an untouched valid split and was localized
  to the registered dispatched stratum rather than the byte-identical controls.
- A11's terminal-evidence recovery was complete on the 96 answerable
  path-required cases for T and E and absent for V, which is the expected
  mechanism signature of traversal.
- The existing QT-4 and A11 forensic records bind their published aggregates
  to immutable local result artifacts. The consolidated ledger now verifies
  every local source hash and records the observed hashes of remote aggregate
  receipts.

### What did not hold up

- QT-1 through QT-3 did not improve accuracy. More references, a summary, or a
  reserved endpoint are not substitutes for selecting the right evidence.
- The archived generality grid has a denominator defect. Its numbers must not
  appear in a landing-page, investor, research, or cross-model claim.
- A11's E-minus-T gain was one question and was confounded by a bundled
  answerability receipt. It cannot license event grouping.
- Token receipts are subscription usage, not provider-priced dollar cost.
  Monetary API economics remain unmeasured.

## Cheating and leakage assessment

No current artifact shows evidence that an answer arm received gold labels,
used tools during answering, or changed the registered grading rule. That is a
bounded statement, not a universal proof: aggregate receipts alone cannot
prove the absence of prompt leakage in every historical run.

A11b closes the recorded in-band receipt gap prospectively, not the
off-channel exposure gap. The prepared content-free audit verifies
all 1,152 prompt hashes, scans sealed prompt bytes for audit-only fields and arm
identities, requires exactly one agent message and one usage receipt per
accepted slot, rejects tool or non-message events, validates retry histories,
and reconciles event, executor, and final-result token totals by arm. It emits
only hashes and counts. Hidden chain-of-thought is neither available nor needed.

Live transport preflight on 2026-07-16 found that r3 could not execute as sealed:
the backend rejects root `oneOf` and `uniqueItems` in response-format schemas.
All rejected probes ended before inference with zero recorded token usage. The
unregistered preview therefore sends only the supported structural subset to
the backend and applies the complete original `oneOf`, uniqueness, pattern,
nonempty-field, and citation constraints offline before accepting an answer.
It also applied 219 narrowly defined deterministic normalizations that cleared
the insufficiency-reason field while preserving answer text and citations.
Many preserved answers were explicit insufficiency statements that the adapter
then misclassified as substantive. This is evidence about transport
compatibility, not a registered A11b protocol result.

The completed preview replay revalidated all 1,152 marker-selected prompt,
schema, answer, event, and usage receipts against controller SHA-256
`86f1bf8e3d8500c76504154f1c1c25d5b31afb499006317d9e2deb104bae8caf`.
The final result SHA-256
`0599d68ae8a344d154b9bb0b6051cb2fc27c63eb9f69b17972066909a6585d68`
matches its immutable manifest. The arm-blind panel made 132 first-attempt
calls and produced 864 unanimous 3-of-3 verdicts with tools disabled. No
recorded artifact indicates gold leakage into the answerer. These checks make
the exploratory aggregate reliable; they do not cure its registration defect
or same-model-family judging limitation.

Because the strict normalized endpoint had no discordant questions, a human
reviewer can inspect a
stratified sample of supported agreements and unsupported failures for
visible-evidence error classification. That review is descriptive and cannot
turn the run into a registered result or override its no-promotion boundary.

## Economics interpretation

- A6a run 2 used 26,207,434 accepted tokens for question-aware selection and
  40,610,756 for blunt projection, a reduction of 14,403,322 tokens (35.5%) in
  the selected arm while also improving correctness.
- QT-1 through QT-3 each used roughly 25.6M to 26.2M accepted tokens and did
  not improve accuracy.
- QT-4 valid374 used 60,760,432 accepted answer tokens and 63,737,565 across
  all attempts; panel judging used another 1,820,656. The aborted v1 protocol
  overhead was 3,546,961 and remains separate.
- A11 used 4,728,676 accepted answer tokens with zero retries; its panel used
  another 445,171.
- The A11b unregistered preview used 24,481,563 accepted answer tokens and at
  least 24,568,225 across all attempts; its panel used another 1,987,299. T1
  cost 2.2% more selected-artifact tokens than T0 while showing a large
  post-hoc semantic-insufficiency gain. E1 cost 3.6% more than T1 without
  incremental observable benefit.

These are per-run receipts. They must not be naively summed when a comparator
artifact was reused rather than rerun.

## Next decision sequence

1. Do not replay this exact A11b corpus across APIs. Its answerable cases are
   ceilinged, its Patients are spent, and its response contract erased the
   measured insufficiency signal.
2. Use the versioned categorical answered/insufficient contract, with no
   post-acceptance semantic normalization.
3. Construct a fresh patient-disjoint development/holdout corpus where T0 has
   measurable answerable-case errors and a development pilot demonstrates
   nonzero paired discordance before the holdout is opened.
4. Run a separately implemented content-free raw-audit adapter and a
   cross-family panel sensitivity check before making any stronger reliability
   claim.
5. Regrade the existing six-cell generality grid without model calls.
6. Freeze the portable cross-API harness only after a packet contrast has
   nonzero headroom; then spend API budget on that exact sealed comparison.

Cross-API hardness benchmarking is intentionally deferred. A native graph
storage-engine comparison is a separate systems experiment and should not be
inferred from this efficacy program.
