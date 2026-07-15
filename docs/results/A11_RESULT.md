# A11 V/T/E path-required efficacy result

Status: **complete - do not promote E over T**

Completed: 2026-07-15

Questions: 120 paired, non-PHI synthetic efficacy questions across 100 patient
clusters

Answer and panel model: `gpt-5.6-sol`, high reasoning effort

## Decision

Bounded reference traversal solved the missing-terminal-evidence problem in this
frozen synthetic corpus. Typed event grouping did not earn an accuracy
promotion over flat traversal.

The registered primary contrast, E minus T over all 120 questions, was positive
but based on one discordant question. Its patient-cluster interval includes
zero, so the registered decision is `do_not_promote_e`.

The registered secondary contrast shows that traversal matters when the answer
is deliberately placed behind a reference path that the vocabulary-star packet
does not contain. It was not a promotion gate.

This experiment compared query-time packet treatments, not database engines.
It does not show that graph-native storage, Neo4j, or Bonfire is more accurate
than Postgres or any other system.

## Arms

- **V - vocabulary star:** the holdout-promoted terminology-aware patient-star
  packet.
- **T - flat traversal:** V plus bounded reference traversal, serialized as a
  flat resource set with path citations.
- **E - event groups:** the identical T retrieval compiled into typed event
  groups with canonical time, temporal rank, typed edges, path citations, and a
  deterministic answerability receipt.

## Registered correctness results

| Arm | Correct | Accuracy | Abstentions |
|---|---:|---:|---:|
| V | 24 / 120 | 20.0% | 120 / 120 |
| T | 119 / 120 | 99.2% | 23 / 120 |
| E | 120 / 120 | 100.0% | 24 / 120 |

### Primary: E minus T over all 120 questions

- Difference: **+0.833 percentage points**.
- Discordant pairs: one E-only correct, zero T-only correct.
- Patient-cluster bootstrap 95% interval: **0.000 to +2.564 points**,
  100 clusters, 10,000 replicates, seed `20260715`.
- Exact McNemar p-value: **1.0**, report-only by preregistration.
- Promotion: **no**. The interval does not exclude zero.

### Secondary: T minus V over 96 answerable questions

- T: **96 / 96**; V: **0 / 96**.
- Difference: **+100 percentage points**.
- Patient-cluster bootstrap 95% interval: **+100 to +100 points**,
  96 clusters.
- Exact McNemar p-value: **2.524e-29**, report-only.
- This contrast was explicitly not a promotion gate.

## Mechanism outcomes

- Terminal-evidence recall on the 96 answerable questions was 0/96 for V and
  96/96 for both T and E.
- T and E used identical retrieved evidence for all 120 questions. The mapping
  SHA-256 is
  `07b655b184b916d3bafe9c28f12ff92b520d800168f75a11ead5dfdf23574eca`.
- Source JSON-pointer replay, shortest-path validation, and normalized UTC
  temporal-rank validation each passed 120/120.
- E selected the registered temporal root correctly on all 114 cases where a
  selection was required. The other six cases were registered bound-exhaustion
  cases with no selection.
- E had zero date-order errors and 120/120 answerability calibration.
- E scored 15/15 in every family/depth cell. T scored 15/15 in seven cells and
  14/15 in `observation_specimen:depth-3`. V scored 3/15 in every cell because
  those were the three unanswerable cases.

## Answer behavior

- E correctly abstained on all 24 unanswerable questions, with no false
  abstentions and no unsupported answers.
- T correctly abstained on 23 unanswerable questions and made one substantive
  answer on the remaining unanswerable question. It had no false abstentions.
- V abstained on all 120 questions. That was correct on the 24 unanswerable
  questions and a false abstention on all 96 answerable questions.
- V produced two source-ID copy errors. They truncated a present root ID by one
  or two hexadecimal characters; neither affected the already-incorrect
  abstention label.

The sealed post-result explanation is in
[`A11_FORENSIC_AUDIT.md`](A11_FORENSIC_AUDIT.md).

## Packet and token economics

| Arm | Model payload bytes | Accepted answer tokens | All-attempt answer tokens |
|---|---:|---:|---:|
| V | 113,520 | 1,493,749 | 1,493,749 |
| T | 556,188 | 1,633,526 | 1,633,526 |
| E | 505,848 | 1,601,401 | 1,601,401 |

E used 50,340 fewer serialized payload bytes than T, a **9.1% reduction**, and
32,125 fewer answer tokens, a **2.0% reduction**. This is a packet-
representation efficiency result, not a database-cost or product-efficacy
result.

Answer generation used 4,728,676 tokens. The arm-blind panel made 30 accepted
calls using 445,171 tokens. The complete answer-plus-panel experiment therefore
used **5,173,847 tokens**. Accepted and all-attempt totals are identical because
there were zero retries and zero failed attempts.

Monetary cost was not preserved as a comparable sealed receipt and is not
reported.

## Compilation timing

The shared governed bundle build had a median of 701,521.5 ns (0.702 ms), p95
of 784,375 ns (0.784 ms), and maximum of 1.796 ms over 120 efficacy questions.
E payload access had a 22.2 microsecond median; T payload access had a 24.5
microsecond median. Full V production over all 144 development-plus-efficacy
rows took 120.647 ms.

These are local compiler timings, not database, network, or production latency
measurements.

## Integrity and reproducibility receipt

- Official controller manifest SHA-256:
  `3f1209ebc750c7f9eeb67d0a7e5ed3a455aa91dbda2be2ffd4c1905fe192fdce`.
- Dataset manifest SHA-256:
  `442ca8d204fbd81f06e0abaf2ea5022b375deabb71a93d5ebaeccef98e99fe3c`.
- Answer-input manifest SHA-256:
  `b98f7e3ae0d3478b001acebb4ac2f29a211e38058eb4d0f0f0b58a5197a167cc`.
- Grading manifest SHA-256:
  `f259cc57e1fc55ae238a8b3aa2cf643c678bd9f39c532a36152ce42f0f19f968`.
- Panel-verdict manifest SHA-256:
  `c4e77a5aea16a0d650c35b2f49b8ef125485baf3fde4f8f1f48e257ab3f7d3d9`.
- Final result SHA-256:
  `95e0dfddbba5aeddd9822f9a9c0d6e3a20c7333fe82b0386d0e26467b6c3b27d`.
- Clean completions: 360/360, 120 per arm.
- Failed attempts and retries: zero.
- Panel: 192 substantive answer items, three votes per item, 30 batched calls.
- All 192 panel majorities were unanimous 3-0.
- Model calls during deterministic finalization: zero.
- Content-addressed raw execution archive SHA-256:
  `1d609dbf96ce28dab2ca59cd9de12e2a79a8cd4fb3caf20c01df6dbb8e477449`.
  The archive and all three raw arm trees were made read-only after their
  sealed hashes were verified.

The exact aggregate result is
[`A11_RESULT.json`](A11_RESULT.json). The preserved grading, panel, and result
manifests are indexed in [`a11-artifacts/README.md`](a11-artifacts/README.md).

## What this establishes, and what it does not

A11 establishes the mechanism prerequisite for bounded traversal: if terminal
evidence is genuinely behind a reference path and absent from the star packet,
retrieving that path can change answerability from zero to complete on this
constructed corpus.

It does not establish an incremental accuracy benefit for typed event groups.
The only E-over-T gain was an unanswerable case, and E bundled event structure
with a deterministic answerability receipt. A follow-up must hold that receipt
constant to isolate structure.

The result is single-model, synthetic, non-PHI, and deliberately path-required.
It does not establish cross-model, cross-server, natural-chart, authorization,
storage-engine, or Bonfire product generality.

The panel was arm-blind but used the same model family as the answering agent.
Seventeen of 30 batches contained multiple responses from the same synthetic
record, so correlated judging remains possible even though no arm identity was
exposed. Filesystem receipts can establish the recorded execution path, not the
absence of any off-channel observation.
