# A11b r3 unregistered exploratory result

Status: **complete exploratory analysis - do not promote**

Completed: 2026-07-16

Population: 384 synthetic, non-PHI efficacy Patients; one paired question per
Patient; 1,152 accepted answer slots across T0, T1, and E1

Answer and panel model: `gpt-5.6-sol`, high reasoning effort

## Decision

The three arms tied exactly. T0, T1, and E1 each scored 288/384 (75.0%), with
zero discordant pairs for both registered contrasts. The patient-cluster
bootstrap interval for E1 minus T1 and T1 minus T0 was exactly 0.0 to 0.0
percentage points.

This result does not support an incremental answer-accuracy benefit from the
deterministic aids in T1 or the typed event grouping in E1. The exploratory
decision-function replay returned `do_not_promote`.

This is not a registered efficacy result. The sealed r3 response schema was
rejected by the current Codex backend before inference because its structured
output contract used unsupported `oneOf` and `uniqueItems` features. The answer
preview therefore used a reduced transport schema, enforced the complete
original schema offline, and was labeled unregistered before its first model
call. Its results cannot be promoted retroactively or presented as
confirmatory.

## What actually happened

| Stratum | T0 | T1 | E1 |
|---|---:|---:|---:|
| 288 answerable questions | 288/288 | 288/288 | 288/288 |
| 96 unanswerable questions | 0/96 | 0/96 | 0/96 |
| All 384 questions | 288/384 | 288/384 | 288/384 |

Every answerable item routed to the arm-blind panel, and all three panel votes
judged all 864 arm-question answers correct. Every unanswerable item was graded
deterministically. All three arms gave a substantive unsupported answer on all
96 rather than abstaining, so all 288 unanswerable arm-question answers were
incorrect.

The benchmark therefore failed to create an accuracy contrast:

- T0 already contained enough visible evidence for the model to answer every
  supported question correctly, leaving no headroom for T1 or E1.
- The only failures were a shared answerability/calibration failure. More
  deterministic aids or event-group structure did not make the model abstain
  when the record was intentionally insufficient.
- This does not show that the graph was constructed incorrectly. It shows that
  this corpus could not distinguish the three representations on supported
  questions and that graph structure did not fix a shared refusal-policy
  failure on unsupported questions.

Every family/depth cell and both temporal-policy strata had the same 75.0%
accuracy in every arm. The six unanswerable difficulty classes each contributed
16 failures per arm; every answerable difficulty class was 16/16 per arm.

## Registered contrast replay

### E1 minus T1

- Difference: **0.0 percentage points** over 384 paired Patients.
- Discordant pairs: zero E1-only correct and zero T1-only correct.
- Patient-cluster bootstrap 95% interval: **0.0 to 0.0 points**, 10,000
  replicates, seed `20260716`.
- Exact McNemar test: not estimable because there were no discordant pairs.
- Exploratory promotion: **no**.

### T1 minus T0

- Difference: **0.0 percentage points** over 384 paired Patients.
- Discordant pairs: zero T1-only correct and zero T0-only correct.
- Patient-cluster bootstrap 95% interval: **0.0 to 0.0 points**, 10,000
  replicates, seed `20260716`.
- Exact McNemar test: not estimable because there were no discordant pairs.
- Exploratory fallback promotion: **no**.

## Packet and token economics

| Arm | Packet UTF-8 bytes | Accepted answer tokens | All-attempt answer tokens |
|---|---:|---:|---:|
| T0 | 25,007,478 | 7,949,679 | 7,991,312 |
| T1 | 26,231,926 | 8,121,049 | 8,121,049 |
| E1 | 28,261,238 | 8,410,835 | 8,455,864 |

T1 used 171,370 more accepted tokens than T0 (+2.2%) and E1 used 289,786
more than T1 (+3.6%) without an accuracy gain. Answer generation used
24,481,563 accepted tokens. The seven additional attempts bring recorded
all-attempt usage to at least 24,568,225 tokens; three pre-inference schema
rejects had no usable provider receipt, so that value is explicitly a lower
bound.

The panel made 132 accepted calls and used 1,987,299 tokens. All 132 calls
succeeded on their first attempt. Accepted answer-plus-panel usage was
**26,468,862 tokens**; recorded all-attempt answer-plus-panel usage was at least
**26,555,524 tokens**.

The preview applied 219 deterministic normalizations: 27 in T0, 96 in T1, and
96 in E1. Each transformed only the mutually exclusive
`insufficiency_reason` field from a non-null string to `null` when a substantive
answer was already present, left the substantive answer unchanged, and then
required the complete original sealed schema to pass. This is a protocol
compatibility signal and one reason the run remains exploratory.

## Integrity and anti-leakage receipt

- Controller SHA-256:
  `86f1bf8e3d8500c76504154f1c1c25d5b31afb499006317d9e2deb104bae8caf`.
- Bundle SHA-256:
  `21fe8fc13d47aec88339bdaecab14a5fb369a9fa73ec187cf81220e5f527ec64`.
- Final result SHA-256:
  `0599d68ae8a344d154b9bb0b6051cb2fc27c63eb9f69b17972066909a6585d68`.
- Clean accepted coverage: 1,152/1,152, exactly 384 per arm.
- Attempts: 1,159; seven operational/schema retries beyond the accepted set.
- The completion replay revalidated each marker-selected answer artifact,
  prompt hash, schema receipt, event-stream receipt, and accepted artifact
  hash. It reported `answers_exposed: false`.
- Accepted calls were required to have one valid answer, one usage receipt,
  empty stderr, and no tool events.
- The panel was arm-blind, used opaque item identifiers, prohibited tools, and
  made 132/132 first-attempt calls. All 864 majority verdicts were unanimous
  3-of-3.
- Deterministic finalization made zero model calls. The result's byte count and
  SHA-256 match its immutable final manifest, whose `all_checks_passed` field is
  true.

These receipts make the aggregate exploratory readout reproducible and reveal
no evidence of gold leakage into the answering agent. They cannot turn a
post-seal transport adaptation into a registered experiment, prove the absence
of off-channel observation, or make the same-model panel independent of the
answering model family.

## Consequence for the next run

Do not spend cross-API budget replaying this exact corpus: its supported cases
are ceilinged and its unsupported cases test a shared abstention policy rather
than graph representation.

The next confirmatory candidate needs a fresh patient-disjoint holdout and must
be frozen only after development data establish that:

1. T0 is genuinely missing or ambiguously presents evidence needed for some
   answerable questions while T1 and E1 remain evidence-equivalent.
2. T1's deterministic aids are held constant in E1 so grouping is the only
   causal difference.
3. Unsupported and partially supported cases have an explicit, arm-identical
   abstention contract that the transport schema can express without
   normalization.
4. The backend-compatible transport schema is sealed up front, with the full
   semantic contract enforced offline.
5. A development pilot demonstrates nonzero paired discordance before the
   untouched holdout or cross-API model matrix is opened.

The 384 efficacy Patients used here are now spent for future confirmatory
claims.
