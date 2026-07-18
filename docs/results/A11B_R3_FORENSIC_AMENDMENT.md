# A11b r3 forensic amendment

Status: **post-result correction; strict artifact preserved; no promotion**

Completed: 2026-07-17

This amendment supersedes the behavioral interpretation in
[`A11B_R3_UNREGISTERED_EXPLORATORY_RESULT.md`](A11B_R3_UNREGISTERED_EXPLORATORY_RESULT.md).
It does not alter that document's sealed result JSON, hashes, normalized labels,
or `do_not_promote` decision.

## Corrected conclusion

The official normalized artifacts reproducibly score T0, T1, and E1 at
288/384. The answerable stratum genuinely ceilinged at 288/288 in every arm;
the compatibility normalizer created the unsupported-stratum tie by erasing
the field used to recognize insufficiency. The overall strict tie is therefore
not a faithful measure of the raw answer behavior elicited by the prompt.

The raw logs show that T1's deterministic temporal and answerability aids
changed insufficiency behavior substantially. E1's event groups added no
observable benefit beyond the identical T1 aids. Because this diagnosis and
the semantic sensitivity rules are post-result, neither feature can be
promoted from this run.

## Raw behavior versus normalized labels

All three arms answered the 288 answerable questions correctly. On the 96
unanswerable questions:

| Raw rule | T0 | T1 | E1 |
|---|---:|---:|---:|
| Nonempty `insufficiency_reason` | 27/96 | 96/96 | 96/96 |
| Answer begins `Insufficient evidence` | 26/96 | 86/96 | 78/96 |
| Answer begins `Insufficient evidence` or `Insufficient data` | 26/96 | 96/96 | 96/96 |
| Exact legacy sentinel plus empty citations | 0/96 | 0/96 | 0/96 |

The preview normalizer cleared all 219 nonempty insufficiency reasons: 27 T0,
96 T1, and 96 E1. The strict grader then treated every one of those selected
artifacts as a substantive response.

The mismatch was mechanical:

1. The prompt requested an explicit insufficiency statement and an explanation.
2. The sealed schema accepted insufficiency only when `answer` was the exact
   literal `Insufficient evidence.`, citations were empty, and the reason was
   nonempty.
3. Natural variants such as `Insufficient data: ...`, or insufficiency answers
   citing the visible path that established what was missing, failed that
   branch.
4. The compatibility normalizer reinterpreted any non-sentinel answer as
   substantive and set its reason to `null`.
5. The grader used the erased reason as its abstention signal.

## Descriptive sensitivity analysis

Using the arm-independent rule that an answer begins `Insufficient evidence`
or `Insufficient data` gives:

| Arm | Correct | Accuracy |
|---|---:|---:|
| T0 | 314/384 | 81.77% |
| T1 | 384/384 | 100.00% |
| E1 | 384/384 | 100.00% |

- T1 minus T0: +18.229 percentage points; 70 T1-only versus zero T0-only
  discordances; exact two-sided McNemar `p=1.6940658945086007e-21`.
- Registered-alpha descriptive bootstrap: 97.5% interval +13.802 to +22.656
  points, 10,000 replicates, seed `20260716`.
- Ordinary descriptive 95% bootstrap interval: +14.583 to +22.135 points.
- E1 minus T1: 0.0 points with no discordances.

This is sensitivity analysis, not a replacement endpoint. A reason-only rule
would additionally credit one contradictory T0 response that supplied an
organism while also claiming a temporal tie prevented a unique answer.

The aggregate result is preserved in
[`A11B_R3_FORENSIC_SENSITIVITY.json`](A11B_R3_FORENSIC_SENSITIVITY.json).
`a11b_forensic_sensitivity.py` reproduces it directly from the exact r3
controller, bundle, preview, and audit trees with zero model calls and emits no
answer, question, resource, or Patient content.

## Integrity findings

The recorded execution path remains strong:

- 1,152/1,152 selected markers and full-schema selected artifacts validated.
- Every selected stream contained exactly one agent message and no tool event.
- Answer prompts contained no gold-only field names or arm metadata literals.
- The arm-blind panel covered exactly 864 answerable items, recorded 2,592
  unanimous votes, and reconciled every receipt.
- Every answerable response contained its reference display alias.
- Selected answer tokens reconcile to 24,481,563; recorded all-attempt answer
  tokens reconcile to at least 24,568,225.
- The aggregate sensitivity binds all 1,152 selected marker/raw-answer pairs
  plus deterministic labels, queue, coverage, and panel verdicts under preview
  input root `a4ad5c729e8681b4c79f98150cb745596eefabe37bf493201157b4519ca9befa`.

There is no evidence of gold leakage or tool-use contamination. However,
`answers_exposed: false` and `gold_opened: false` were self-asserted fields, not
independent measurements. They cannot prove the absence of off-channel human
or process access and must not be cited as doing so.

One marker-selected T0 artifact originated from a receipt recorded as a
provider failure and was accepted later through deterministic normalization.
That selection was deterministic under the post-seal adapter, not evidence of
answer cherry-picking, but it reinforces the run's unregistered boundary.

## Additional contract defects

The adversarial sweep also established:

- the legacy correctness panel omitted the required `evidence_summary`;
- the legacy shared answer schema allowed contradictory substantive-answer and
  insufficiency states;
- the generic packet renderer failed to reject several answerability and audit
  synonyms;
- the reported temporal-binding metric was a selected-path citation proxy, not
  a complete temporal correctness metric;
- the machine-readable zero-critical-safety-failure field was not enforced;
- A11b intervals used contrast alpha 0.025 and therefore have 97.5%, not 95%,
  coverage.

Aggregate review found no contradictory answer/reason state in the completed
A11 run, so that legacy schema hole did not change A11's published labels.

## Successor gate

Before another answer-model call:

1. Use a categorical `status: answered | insufficient` contract.
2. Require `answer` text and at least one visible FHIR citation only for
   `answered`; require `answer: null` and a non-whitespace reason for
   `insufficient`. Insufficiency citations may document the visible path that
   establishes missing evidence.
3. Bind identical state semantics into the prompt, transport schema, offline
   validator, deterministic grader, behavior metrics, and panel payload.
4. Include `evidence_summary` in panel judgment and test internal consistency.
5. Require zero absolute critical failures in addition to treatment-versus-
   reference non-increase.
6. Generate a fresh patient-disjoint corpus. The r3 efficacy Patients are spent.
7. Require nonzero development discordance before sealing a new efficacy split.
8. Freeze the sensitivity definition prospectively; do not reuse this post-hoc
   wording rule as a confirmatory endpoint.

The successor answer-boundary groundwork is versioned separately from the
sealed r3 contract so historical artifact replay remains possible. It is not a
sealed or runnable experiment yet: a future controller must bind the verified
prompt record, structural transport schema, offline full validator, successor
grading and panel modules, all-strata safety evidence, and fresh corpus.
