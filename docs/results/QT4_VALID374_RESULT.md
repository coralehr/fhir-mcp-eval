# QT-4 valid374 confirmatory holdout result

Status: **complete — promote vocabulary only**

Completed: 2026-07-14

Questions: 374 untouched valid-split questions, three paired arms

Registered microbiology stratum: 44 questions

Negative-control stratum: 330 questions

Answer and panel model: `gpt-5.6-sol`, high reasoning effort

## Decision

Fixed question-only microbiology vocabulary passed every registered H1 gate and
is promoted for this dataset, model, and harness. Bounded exact-reference
traversal improved evidence recall and had a favorable correctness estimate,
but it did not pass the registered significance and interval gates. Traversal
is therefore **not** promoted as an accuracy improvement.

Neither arm tested a persistent graph database. The result does not support a
Postgres-versus-graph-store claim or generality beyond this benchmark and
model.

## Registered correctness results

### Dispatched microbiology stratum

| Arm | Correct | Accuracy | Abstentions |
|---|---:|---:|---:|
| A6a-r (qo-v2.1) | 10 / 44 | 22.7% | 26 / 44 |
| QT-4V: fixed vocabulary | 25 / 44 | 56.8% | 15 / 44 |
| QT-4T: vocabulary + traversal | 29 / 44 | 65.9% | 9 / 44 |

- **QT-4V minus A6a-r: +34.1 percentage points.** Discordant pairs
  16 favorable / 1 unfavorable; exact McNemar **p=.000274658**;
  patient-cluster bootstrap 95% interval **+17.9 to +50.0 points**.
- **QT-4T minus QT-4V: +9.1 percentage points.** Discordant pairs
  5 favorable / 1 unfavorable; exact McNemar **p=.21875**;
  patient-cluster bootstrap 95% interval **0.0 to +20.9 points**.

The fixed-sequence decision was `promote_vocabulary_only`: H1 passed, so H2
was tested; H2 did not pass its exact-McNemar or interval gates.

### Negative controls and pooled safety

| Stratum | A6a-r | QT-4V | QT-4T |
|---|---:|---:|---:|
| Negative controls (n=330) | 191 / 330 (57.9%) | 188 / 330 (57.0%) | 190 / 330 (57.6%) |
| Pooled (n=374) | 201 / 374 (53.7%) | 213 / 374 (57.0%) | 219 / 374 (58.6%) |

The negative-control differences were -0.9 points for vocabulary versus A6a-r
and +0.6 points for traversal versus vocabulary, both within the registered
one-point safety bound. The corresponding pooled differences were +3.2 and
+1.6 points.

The 330 negative-control packets and rendered prompts were byte-identical
across arms before answering.

## Mechanism outcomes

On the 32 dispatched questions with mapped gold resources, id-weighted recall
moved from **36/115 (31.3%)** for A6a-r to **49/115 (42.6%)** for vocabulary and
**85/115 (73.9%)** for traversal. Any-gold coverage moved 5/32 -> 32/32 ->
32/32; full-gold coverage moved 4/32 -> 21/32 -> 27/32.

Vocabulary gained 45 gold-resource occurrences and lost 32. Traversal gained
36 and lost zero. The traversal walker fetched 159 resources, while 495 target
attempts were missing and 337 were stopped by the registered resource cap;
none hit the byte cap. Serialized path receipts were concentrated in
`Observation.hasMember` (242) and `Observation.specimen` (525).
`DiagnosticReport.result` and `DiagnosticReport.specimen` contributed no
serialized paths.

The footprint moved in the desired direction for vocabulary:

| Arm | Packet JSON bytes | Resources |
|---|---:|---:|
| A6a-r | 39,279,441 | 42,934 |
| QT-4V | 32,620,630 | 35,866 |
| QT-4T | 32,929,708 | 36,025 |

Vocabulary removed 6,658,811 packet bytes and 7,068 resources versus A6a-r.
Traversal added 309,078 packet bytes and 159 resources versus vocabulary.

## What the answer-level audit found

The sealed post-result audit found that vocabulary's gain came mainly from
avoiding the generic Observation fallback used when the baseline planner did
not bind local microbiology terms. Vocabulary changed the packet from a broad
firehose to a fixed culture/Gram-stain/screen/smear union. Among the 17
discordant vocabulary-versus-baseline answers, 16 favored vocabulary and one
favored baseline; six favorable flips were abstention-to-correct and ten were
wrong-to-correct.

Traversal changed correctness only on questions where it gained mapped gold
evidence: five flips favored traversal and one favored vocabulary. Four of the
five gains were abstention-to-correct. The one loss was a temporal-binding
failure: traversal recovered much more linked evidence, but the flat appended
packet allowed the answerer to select an older root for a “last” question.

This locates the next bottleneck after retrieval: event grouping, temporal
ranking, salience, deterministic answerability, and aggregation. It argues
against simply increasing traversal depth.

See [QT4_VALID374_FORENSIC_AUDIT.md](QT4_VALID374_FORENSIC_AUDIT.md) for the
integrity and no-cheating review.

## Token economics

### Answer generation

| Arm | Accepted input | Accepted output | Accepted total | All-attempt total | Archived retries |
|---|---:|---:|---:|---:|---:|
| A6a-r | 22,137,555 | 160,062 | 22,297,617 | 23,622,215 | 20 |
| QT-4V | 19,050,084 | 142,792 | 19,192,876 | 20,159,906 | 20 |
| QT-4T | 19,125,336 | 144,603 | 19,269,939 | 19,955,444 | 17 |

Vocabulary reduced accepted answer tokens by **3,104,741 (13.9%)** versus
A6a-r. Traversal used 77,063 more accepted tokens than vocabulary. On
all-attempt accounting, vocabulary saved 3,462,309 tokens versus A6a-r and
traversal used 204,462 fewer tokens than vocabulary because it had fewer
discarded attempts.

Accepted answer generation totaled **60,760,432 tokens**. All answer attempts,
including the 57 archived failed attempts, totaled **63,737,565 tokens**.

### Panel and aborted-protocol overhead

The pinned arm-blind panel accepted 120 calls and used **1,820,656 tokens**.
The complete v2 experiment therefore used **65,558,221 all-attempt answer and
panel tokens**.

The separately aborted v1 controller consumed **3,546,961 tokens** across 61
completed provider turns: 1,315,314 in the A6a-r namespace, 1,107,649 in
QT-4V, and 1,123,998 in QT-4T. Those tokens are protocol overhead only and
never enter arm efficacy or arm-economics comparisons. Including that disclosed
overhead, the program consumed **69,105,182 tokens** for v1 plus the complete
v2 answer and panel work.

Monetary cost and wall time were not preserved as comparable receipts, so no
dollar or duration estimate is reported.

## Integrity and reproducibility receipt

- 1,122/1,122 accepted sealed answers: 374 per arm.
- 57 archived failed attempts; zero invalid current receipts.
- Controller manifest SHA-256:
  `ed5e27e2de7cbec71caf5cefe1d1d8c90c7ba2250e1c494ded76d0e3fde15605`.
- Holdout input SHA-256:
  `22e914e410ab2cc8eb0c1df2bf2286f42a88e86683117263d7cc0f17a7b402b6`.
- Packet SHA-256 values: A6a-r
  `5ce86c68b7fbbfde6b564f5acad2132f0f0fc41f423c215d1bf39d32f85c03cc`,
  QT-4V
  `e50b03203a96809f58cbafb63144f380b16366eacf69a70babd583a1cd361099`,
  QT-4T
  `39bdcac278c0fcb72f78e32cc219a391033093b6e3d108e22621e64bb4ed57af`.
- Grading manifest SHA-256:
  `c7c84246cb82f9bf36779fa0d4caed37092efb2677d828588a3822e4ab516489`.
- Completion receipt-set SHA-256:
  `0ef70b5df66c45744581da3fba68efe8fee18373c4f7e88f9629834ca4ff27d5`.
- Archived failed receipt-set SHA-256:
  `a2c568487dddee3f1e91ea4fbd3ea87153f6df84915195e44e23f9e5e98359f0`.
- Panel queue SHA-256:
  `640103e691773be412d3edb416ab934af67236f4cae790740a164fa53cdc73be`.
- Panel cache SHA-256:
  `08fdf9b09c8d6db10858a70c92895a46579ff2c568ad4ee3e6df7131f9191683`.

The aggregate machine-readable companion is
[QT4_VALID374_RESULT.json](QT4_VALID374_RESULT.json). It intentionally excludes
question IDs, answer content, patient/resource identifiers, and raw FHIR.

## Licensed claim

> On an untouched 374-question FHIR-AgentBench valid-split holdout, fixed
> question-only microbiology vocabulary improved correctness on the registered
> 44-question stratum from 10/44 to 25/44 (+34.1 points, patient-cluster 95%
> interval +17.9 to +50.0, exact McNemar p=.000275) while the 330 negative
> controls remained within the registered safety bound. Vocabulary is promoted
> for this dataset, model, and harness. Bounded exact-reference traversal
> reached 29/44, but its +9.1-point increment versus vocabulary was unresolved
> (p=.219) and is not promoted.

## Next experiment

Use the promoted vocabulary arm as the baseline for A11. Compile each root
microbiology event with linked children/specimen, canonical event time,
explicit first/latest rank, typed edge labels, path citations, and a
deterministic answerability receipt. Test that event-group packet on a sealed
path-required dataset; do not widen or deepen generic traversal first.
