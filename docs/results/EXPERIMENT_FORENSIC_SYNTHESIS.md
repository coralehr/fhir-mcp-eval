# Experiment program forensic synthesis

Date: 2026-07-16

## Bottom line

The program supports two product claims and one bounded mechanism claim:

1. Deterministic question-aware selection beat query-blind projection twice on
   the paired 409-question FHIR benchmark.
2. A fixed microbiology vocabulary improved the selected packet on an
   untouched valid-split dispatched stratum.
3. Bounded traversal recovered terminal evidence on A11's constructed
   path-required benchmark.

The evidence does **not** yet establish that event grouping improves accuracy,
that the effects generalize across model families or APIs, or that Bonfire
should use a native graph database. A11b is the registered causal isolation
test for event grouping. It has 64 development Patients, 384 untouched efficacy
Patients, one efficacy question per Patient, and 1,152 answer slots across T0,
T1, and E1. It has not made a model call.

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
| A11b | r3 is sealed but its response schema is rejected by the current backend before inference. An explicitly unregistered preview uses a structural transport schema and enforces the full registered schema offline. | No claim from the preview; an official/API run requires a new compatible seal. |

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

A11b closes that gap prospectively. The prepared content-free audit verifies
all 1,152 prompt hashes, scans sealed prompt bytes for audit-only fields and arm
identities, requires exactly one agent message and one usage receipt per
accepted slot, rejects tool or non-message events, validates retry histories,
and reconciles event, executor, and final-result token totals by arm. It emits
only hashes and counts. Hidden chain-of-thought is neither available nor needed.

Live transport preflight on 2026-07-16 found that r3 cannot execute as sealed:
the backend rejects root `oneOf` and `uniqueItems` in response-format schemas.
All rejected probes ended before inference with zero recorded token usage. The
unregistered preview therefore sends only the supported structural subset to
the backend and applies the complete original `oneOf`, uniqueness, pattern,
nonempty-field, and citation constraints offline before accepting an answer.
This is evidence about transport compatibility, not an A11b protocol result.

After the machine gate passes, a human reviewer should inspect the discordant
question set plus a stratified sample of agreements for visible-evidence error
classification. That review is descriptive and cannot override the registered
promotion decision.

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

These are per-run receipts. They must not be naively summed when a comparator
artifact was reused rather than rerun.

## Next decision sequence

1. Treat the running subscription preview as exploratory only; approval cannot
   retroactively promote it.
2. Build and seal a backend-compatible official/API transport schema while
   retaining full offline validation of the registered answer contract.
3. Export the completed trusted-executor run and execute the prepared
   `raw-audit` before opening gold or interpreting correctness.
4. Finalize registered grading, then execute `final-report`. Promote E1, promote
   T1 only, or stop the event-grouping accuracy thesis exactly as registered.
5. Regrade the existing six-cell generality grid without model calls.
6. Only then freeze the portable cross-API harness and spend API budget on the
   winning exact packet comparison.

Cross-API hardness benchmarking is intentionally deferred. A native graph
storage-engine comparison is a separate systems experiment and should not be
inferred from this efficacy program.
