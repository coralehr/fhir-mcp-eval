# A11 sealed controller gate

Status: **official Mac mini controller sealed; live answer run launched; final result pending**

Date: 2026-07-15

Answer and judge calls during implementation review and the official seal: **0**

## Official execution-host seal and launch

On 2026-07-15, the official Mac mini controller was sealed from merged commit
`0123ca2bf4e1aecfdf5092b0a2b333c5afbe75dc` against dataset manifest
`442ca8d204fbd81f06e0abaf2ea5022b375deabb71a93d5ebaeccef98e99fe3c`.
The resulting controller manifest has SHA-256
`3f1209ebc750c7f9eeb67d0a7e5ed3a455aa91dbda2be2ffd4c1905fe192fdce`.
It binds Python `3.14.5`, Codex CLI `0.144.1`, Codex binary SHA-256
`134063e133f0b4244fa3b251acf973d4fe4b4aeeacbdc135211bf480f59f1477`,
model `gpt-5.6-sol` at high reasoning effort, and 120 efficacy questions across
three arms for exactly 360 scheduled answer calls.

The sealed runner passed a zero-call `--live --max-attempts 0` smoke and the
single live controller was then launched as PID `64151`. That PID is a
historical launch receipt, not controller identity or proof of continued
liveness; immutable, content-free `--status` receipts are authoritative for
progress. No answer content has been inspected. Correctness, uncertainty,
economics, and promotion status remain unknown until the registered grading,
panel, audit, and finalization sequence completes.

## What is now sealed

`run_a11_experiment.py` independently replays the registered answer inputs from
dataset manifest
`442ca8d204fbd81f06e0abaf2ea5022b375deabb71a93d5ebaeccef98e99fe3c`,
binds every source byte before validation, snapshots the exact V/T/E prompts,
schema, controller, panel and grading runtime, and freezes the rotating
360-answer schedule. The live runner re-executes from an immutable pre-import
bootstrap, adopts one inherited singleton lock, rehashes the Codex binary before
every call, and permits retries only for the exact answerless provider-failure
event shape.

The answer prompt exposes only question id, question, assumption and the exact
arm payload. Governance identifiers and arm labels remain hidden. Opaque
synthetic FHIR resource ids and Patient references remain visible in every arm
because preserving graph topology is the treatment; they contain no PHI and do
not encode answers.

Gold remains inaccessible until the controller proves 120 questions x 3 arms
of clean, hash-bound completion receipts. Structured abstentions are graded
deterministically. Substantive answerable responses enter the exact pinned
three-vote arm-blind panel. Substantive responses to unanswerable items are
deterministically incorrect and never enter an undefined categorical rubric.

The panel runs in an empty non-repository working directory, rejects every tool
event, retries only exact answerless provider failures, and seals its completed
tree read-only. Finalization replays every expected vote/batch/attempt artifact,
rejects unknown receipts, derives each three-vote majority again, and never
trusts a self-hashed verdict map.

## Registered result

The finalizer reports:

- primary E minus T correctness over all 120 efficacy questions;
- secondary T minus V correctness over 96 answerable questions;
- 10,000-replicate patient-cluster bootstrap intervals and report-only exact
  McNemar tests;
- terminal-evidence recall, exact source JSON-pointer path replay, selected-root
  and normalized-time outcomes, answerability calibration and packet bytes;
- abstention, unsupported-answer and citation outcomes;
- accepted/all-attempt answer and panel token economics, retry yield, and
  compilation min/median/p95/max; and
- exact eight-cell family/depth results and the frozen promotion decision.

Unreconciled accepted/all-attempt answer tokens block gold access. Unreconciled
panel economics is a critical safety failure and prevents promotion.

## Verification

The final reviewed tree passed 185 relevant no-model unit/regression tests,
Python compilation, `git diff --check`, an end-to-end zero-model seal, immutable
`--status` re-execution, a `--live --max-attempts 0` no-call smoke, and duplicate
lock rejection. Independent adversarial review returned **APPROVE FOR SEAL**
with no remaining P0/P1/P2 protocol or correctness findings.

The earlier local rehearsal manifest remains non-authoritative. The official
execution-host seal above now binds the Mac mini Codex path, version and binary
hash. No accuracy claim exists until that controller completes and the
registered finalizer runs.

## Remaining execution order

1. Let the single sealed `--live` controller finish; inspect only content-free
   `--status` receipts while answers are in flight.
2. Run `--prepare-grading` only after 360 clean completions.
3. Execute the snapshotted `run_a11_panel.py --live` until all registered votes
   complete, then use its read-only `--audit` replay.
4. Run controller `--finalize` and publish the immutable result artifacts.
