# A11b successor development preregistration

Status: **prospective development phase; no answer-model calls made and no
efficacy packet materialized**.

Date: 2026-07-17

## Amendment 1: recorded lossless transport adaptation

**Dated 2026-07-28, before any successor answer-model call.** Independent
review in issue #100 identified that the v2 categorical contract validated
provider output fail-closed but did not preserve an explicit
transport-to-canonical adaptation record in the development result. The
2026-07-18 sealed candidate is therefore retired with zero answer-model calls.
It may not be launched under this amendment.

The amended development protocol keeps the same two semantic states and the
same correctness endpoint. It adds one identity-only adaptation boundary:

- the exact raw provider bytes remain committed in the witnessed executor
  archive;
- the parsed transport payload and validated canonical answer are recorded for
  every accepted call;
- an `a11b-answer-adaptation-receipt-v1` binds the raw transport SHA-256 and
  both canonical payload SHA-256 values;
- the two canonical hashes must match, and no coercion, defaulting, field
  deletion, or post-hoc normalization is permitted;
- unknown states, duplicate JSON keys, non-finite values, invalid UTF-8,
  contradictory states, and receipt tampering fail before scoring; and
- the result manifest binds the ordered 192-record adaptation set through
  `answer_adaptations_sha256`.

The amended protocol uses
`a11b-successor-development-exact-alias-grading-v2`,
`a11b-successor-development-result-manifest-v3`, and
`a11b-successor-development-discordance-gate-v2`. It requires a newly built
controller, installation package, exact-head approval, external anchor, and
content-free readiness receipt before a model call. The failed historical r3
experiment and its post-hoc sensitivity result remain separately reported and
are not overwritten.

This document freezes the only development probe allowed to decide whether a
fresh A11b confirmatory efficacy run is worth opening. It does not authorize or
seal that efficacy run.

## Fresh source and split boundary

- Synthea `v4.0.0`, commit
  `0185c09ea9d10a822c6f5f3ef9bdcbcbe960c813`, dependency JAR SHA-256
  `ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1`.
- Temurin 21.0.11+10 macOS ARM64 archive SHA-256
  `6ebcf221c9b41507b14c098e93c6ead6440b8d9bd154f8ec666c4c73abbdb201`.
- Seed and clinician seed `20260718`; population 448; reference/end date
  `2026-07-17`; locale `en-US`; process locale `en_US.UTF-8`; timezone UTC.
- Per-file output is bounded at 128 MiB and the complete output at 4 GiB. The
  initial unsealed infrastructure rehearsal used the historical 64 MiB bound
  and was quarantined after one of 448 bundles measured 72,770,068 bytes. No
  answer content or model output existed; the successor bound was versioned
  before a valid generation receipt or corpus was produced.
- Two independently staged and generated roots must produce byte-identical
  generation specs and receipts. The new raw-output content hash and receipt
  hash must differ from the spent r3 generation.
- The power-gated assignment reserves 64 development Patients and 384
  patient-disjoint efficacy Patients. The development builder may compute the
  reservation count, but it must not construct or materialize an efficacy case,
  packet, gold row, or audit row before the development gate passes.
- Distinct raw-source and receipt hashes prevent reuse of the spent r3 corpus.
  Cross-generation synthetic Patient-ID non-overlap is not claimed because the
  historical raw identifier manifest was not retained; patient disjointness is
  enforced between development and efficacy inside this successor population.

## Frozen development probe

The probe contains exactly one question from each of the 64 development
Patients and exactly three paired answer arms per question, for 192 answer
calls before transport retries:

1. T0: flat bounded traversal with path citations.
2. T1: T0 plus canonical temporal rank, selected-event marker, and deterministic
   answerability receipt.
3. E1: T1 plus typed event grouping with identical clinical evidence.

All arms use `a11b-answer-contract-v2`, the successor prompt protocol, and a
native structural response schema. No prose sentinel or post-hoc response
normalization may change answer state. The recorded transport adaptation is
identity-only and may not change answer state or any other canonical field.
Development correctness is entirely
deterministic and authorizes zero panel calls: an answerable response is correct
only when its validated categorical state is `answered` and its Unicode-NFKC,
whitespace-collapsed, case-folded answer equals exactly the registered code or
display alias. Explanatory prose around an alias is incorrect. An unanswerable
response is correct only when its validated categorical state is `insufficient`.
This conservative endpoint prevents answer text from becoming judge-prompt
instructions and prevents same-model self-grading.

All model-controlled fields fail closed at registered bounds before grading:
the answer is at most 128 UTF-8 bytes, the evidence summary and insufficiency
reason are at most 1,024 UTF-8 bytes each, and there are at most 16 cited
resource IDs of at most 128 UTF-8 bytes each.
JSON Schema `maxLength` is a transport/code-point ceiling; the mandatory
offline contract applies the stricter UTF-8 byte ceiling. A multibyte value can
therefore pass transport validation and still fail closed before grading.

## Fail-closed decision rule

Correctness discordance is evaluated only after all 192 development outcomes
are complete and bound to one witnessed
`a11b-successor-development-result-manifest-v3`. That manifest commits to the
exact audit manifest and gold rows, assignment and outcome arrays, question
count, ordered arms, the complete transport-adaptation record set, accepted and
all-attempt token receipts, per-arm token economics, provider-failure counts,
retry yield, and completeness of both accepted-attempt and all-attempt token
receipts. The efficacy split may be
opened only if both registered paired
contrasts contain at least one discordant correctness pair:

- primary: E1 versus T1;
- secondary: T1 versus T0.

This is a headroom check, not an efficacy estimate. Direction and magnitude are
ignored. If either contrast has zero discordant pairs, the three-arm efficacy
run publishes a deterministic `status="failed"` gate receipt with the grading
and token-economics commitments, then stops. The team may redesign using a new
prospective source generation, but may not tune against or relabel the reserved
384 Patients.

Passing the development gate does not itself authorize model calls on efficacy.
It permits a separate zero-model, independently reproduced efficacy build and a
new exact controller/preregistration seal. The original fixed 0.025/0.025 alpha
split, 10-point minimum effects, 90% target power, and 384-patient selection
remain unchanged unless a new power specification is approved before efficacy
is opened.

## Required receipts before a development call

- byte-identical clean-root generation receipts;
- byte-identical development public and audit manifests;
- proof that no efficacy artifact was materialized;
- exact answer-contract, prompt, schema, runtime, executable, packet, grader,
  identity-adaptation, witness, and executor hashes;
- externally anchored controller bytes with independent exact-head approval;
- a content-free dry-run and full token-accounting path.

Until those receipts exist, model calls remain unauthorized.
