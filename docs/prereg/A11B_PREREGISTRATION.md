# A11b preregistration — event grouping with aids held constant

Status: sealed before any A11b answer or panel model call.

## Question and arms

Does typed event grouping improve correctness beyond flat traversal when the
clinical evidence, path citations, temporal selection aids, and deterministic
answerability receipt are held constant?

- `T0`: flat bounded traversal with path citations.
- `T1`: T0 plus canonical temporal rank, selected-event marker, and the
  deterministic answerability receipt.
- `E1`: the identical T1 clinical evidence and aids represented as typed event
  groups.

The primary contrast is E1 minus T1. The secondary contrast is T1 minus T0.
Both use all 384 efficacy questions. The familywise alpha is 0.05 with a fixed
0.025/0.025 split; no unadjusted alternative analysis can promote an arm.

## Frozen corpus

- Synthea `v4.0.0`, commit
  `0185c09ea9d10a822c6f5f3ef9bdcbcbe960c813`, dependency JAR SHA-256
  `ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1`.
- Temurin 21.0.11+10 macOS ARM64 archive SHA-256
  `6ebcf221c9b41507b14c098e93c6ead6440b8d9bd154f8ec666c4c73abbdb201`.
- Seed and clinician seed `20260716`; population 448; reference/end date
  `2026-07-15`; exactly one FHIR transaction Bundle per living Patient.
- Two independent builds produced the byte-identical generation-receipt
  SHA-256 `246d9dc82e27c237629099a01305e9ca65fa4ed49c1beb253803c08c57bc601a`
  and raw-output content SHA-256
  `273e83b72ecd3a5069ea8d10975ec3bffcc16d9b083995fd321e1a7fe2cfc3d2`.
- The patient-disjoint split is 64 development Patients and 384 untouched
  efficacy Patients, one efficacy question per Patient.
- Public model-input manifest SHA-256:
  `c779c1e0c4e090a2f17c8856fef2999332f0ab1fd3106831dd945b0ef0863e0a`.
- Physically separate audit manifest SHA-256:
  `166f0e69819aa020f5390801ab39540750b51eb88865c113e39c8fe751fc6f67`.
- Independent construction from both generation roots produced byte-identical
  public and audit trees.
- The corpus derivation key is publicly derived from the pinned generation and
  power receipts; there is no operator-selectable nonce or seed to grind.

The efficacy split has eight family/depth cells with 48 questions each,
192 `first` and 192 `latest` questions, 288 answerable and 96 unanswerable
questions. Each of the six registered insufficiency modes occurs 16 times:
selected-path incomplete, temporal tie, precision ambiguity, missing clinical
time, conflicting effective fields, and temporal overlap. Every question has
three plausible event roots. Gold answers, audit-only answerability labels,
audit failure-mode fields, selected terminal identifiers, raw Patient
identifiers, original source-resource identifiers, and Patient demographics are
absent from every public answer input and prompt packet. Deterministically
opaque FHIR resource IDs remain so citations and references are lossless. T1
and E1 intentionally expose the identical
deterministic answerability receipt, including its state and reason; that
receipt is a registered treatment feature, not audit gold.

## Execution

- Answer model: `gpt-5.6-sol`, reasoning `high`, timeout 900 seconds.
- Exactly 1,152 accepted answer slots in a rotating T0/T1/E1 schedule. Each arm
  occupies each within-question order position 128 times.
- Native Codex: `codex-cli 0.144.1`, SHA-256
  `29915529b97697def1a957b0505e770aa6a45744435d62fc263e98d7619e167a`.
- The trusted executor permits at most three operational attempts per slot,
  reserves every call in a signed monotonic witness ledger, and guarantees at
  most one process spawn per attempt. Captured invalid answers are not silently
  resampled.
- Accepted and all-attempt token usage are derived only from one complete
  `turn.completed.usage` receipt and are reported separately by arm.
- No call starts until the exact controller and service bundle are externally
  anchored in a uniquely merged main-branch PR with exact-head approval from a
  pinned independent reviewer and a separate checker signature.
- A root-owned, no-argument nightly runner is part of that exact code binding.
  After the one approved launch it can only advance the sealed answer witness,
  registered grading, derived witnessed panel, and finalization workflow; it
  accepts no caller-supplied prompt, model, arm, queue, vote, or retry input.

## Grading and statistics

The automated grading workflow does not open audit gold until exact 384-by-3
completion coverage and every answer artifact receipt validate. Structured
abstentions are graded deterministically:
correct on registered insufficient cases and incorrect on answerable cases.
Substantive categorical answers are judged by a pinned, arm-blind three-vote
`gpt-5.6-sol/high` panel in batches of 20 with a 600-second timeout.
The exact panel queue and its signed witness schedule are deterministically
derived by the pre-answer-sealed postprocessor only after answer completion;
there is no post-answer operator choice of panel items, batching, prompts, or
votes.

For each contrast, report the paired accuracy difference, discordant counts,
the exact two-sided McNemar result, and a deterministic 10,000-replicate
Patient-cluster percentile interval at contrast alpha 0.025. McNemar is
report-only. Also report temporal-binding errors, unsupported answers,
citation/path failures, false abstentions, answerability calibration, packet
bytes, compilation time, accepted/all-attempt tokens, retry yield, family,
depth, temporal difficulty, and answerability strata.
Difficulty and answerability breakdowns are descriptive and cannot promote an
arm because within-question arm order was balanced globally, not separately
inside every difficulty cell. Compilation latency is measured in a separate
zero-model benchmark and is not an answer-correctness promotion gate.

Promote E1 only if E1 minus T1 is favorable, its registered cluster interval
excludes zero in the positive direction, and E1 does not increase unsupported
answers, citation failures, or temporal-binding errors. If E1 fails but T1
passes its registered contrast and safety checks, promote T1 without an event-
group accuracy claim. If neither passes, stop event grouping as an answer-
accuracy thesis and retain it only for a separately registered auditability,
compression, usability, or latency result.
