# C3G throwaway probe notes

Date: 2026-07-25

Status: development-only exploration on burned, deidentified MIMIC-derived
questions. This is not a confirmatory result or a product claim.

## Question

Does graph closure help after holding the selected flat roots constant, and if
not, which graph/compiler operation is actually missing?

## Zero-model census

On the fixed 24-question burned slice, generic bounded outbound closure added
37 resources across 21 questions:

- 15 Encounter
- 14 Patient
- 8 Medication

Thirteen questions hit the 96-edge cap. The first implementation also let
already-present `subject` and `encounter` edges crowd out newly fetched
evidence; question-only edge-family scheduling and fetched-first citations
fixed that mechanical error.

All 164 attempted Specimen targets across the three microbiology questions
were missing from the loaded Medplum substrate. No traversal algorithm can
recover a target that is absent from the source snapshot.

An adversarial audit then caught a semantic collision in the first census.
“Current visit” can mean either the visit active at a simulated historical time
or an admission still open in the final database snapshot. The benchmark SQL
uses the latter (`dischtime IS NULL`), while the first selector used period
containment. The prototype now exposes both policies instead of silently mixing
them:

| Current-visit policy | Selected | No matching visit | Unresolved | Resources removed |
|---|---:|---:|---:|---:|
| Benchmark snapshot-open | 93 | 23 | 0 | 6,477 |
| Product historical-as-of | 98 | 13 | 5 | 5,496 |

The 23 snapshot-open questions contain only `finished` hospital Encounters.
Their benchmark answers are 20 empty results and three zero/false aggregates.
Historical A6a grading was 11 correct and 12 wrong. That is a concrete
retrospective error set for an answer probe, not an estimated treatment effect.

These figures describe packet pruning only. The prototype never performs a
complete store-level Encounter-to-event query, so it cannot claim a global
absence or recover an event missing from the flat roots. The receipt therefore
says “none in the supplied packet” and records a compiler hash.

## Answer probes

The first medication smoke case (`1436276ff4d05d626c742fa4`) failed in both
the flat and outbound-closure packets. Both answered approximately 687.37
hours, while the benchmark answer is empty because no admission is active at
the authoritative time. Outbound reference traversal resolved Medication
names but did not solve the inverse join from the requested visit to its
events.

The first reverse-join implementation also exposed a selector defect: when no
Encounter contained the authoritative time, it fell back to the chart's final
Encounter, including future Encounters. The corrected encounter-scoped pruning
never falls back to an unrelated visit. It also refuses to infer currency from
the presence of only one Encounter.

Seven additional Codex-subscription episodes compared flat, outbound closure,
and inverse Encounter scoping on three mechanism cases:

| Question | Flat | Outbound | Inverse Encounter |
|---|---|---|---|
| Named procedure in first visit | correct | correct | correct |
| No active stay / glucagon | wrong in prior smoke | wrong in prior smoke | correct negative evidence |
| Generic last procedure | wrong | wrong | wrong |

The encounter-scoped packet reduced input tokens substantially:

| Question | Flat input | Outbound input | Inverse input |
|---|---:|---:|---:|
| Named procedure | 89,901 | 93,803 | 46,249 |
| Generic last procedure | 23,096 | 26,578 | 18,891 |
| No active stay | 83,744 | 92,237 | 14,954 |

These are subscription usage receipts from one non-seeded episode per cell,
not API prices or independent replicates. They used the earlier
historical-as-of semantics; the policy split above was discovered afterward.

## Why the remaining case failed

For the generic “last procedure” question, the correctly selected visit packet
contains all of the following:

- the benchmark's ICD-coded Procedure at `2157-12-19T00:00`;
- a CT bedside Procedure beginning at `2157-12-19T20:19`;
- a PICC bedside Procedure ending at `2157-12-20T14:27`.

The benchmark SQL silently restricts “procedure” to `procedures_icd`, but the
FHIR conversion represents both ICD procedures and bedside `d-items` as FHIR
`Procedure`. The model chose a later bedside event. This is a semantic-family
and provenance collision, not missing graph reachability.

## Neutral event-catalog probe

Across all 409 projected packets, the same FHIR `Procedure` type contains 678
events from three source conventions:

| Derived family | Events | Time representation |
|---|---:|---|
| Inpatient ICD-coded procedure | 176 | `performedDateTime`, day precision |
| ICU bedside event | 304 | `performedPeriod`, minute precision |
| ED/SNOMED workflow event | 198 | `performedDateTime`, minute precision |

The input projection has already removed `meta.profile`, so the throwaway
catalog derives these families from the dataset's code-system conventions and
records that limitation. It is additive: it preserves every FHIR resource,
does not choose a preferred family for generic “procedure” language, and marks
conflicting signals unknown.

The next eight-question burned probe compares the encounter-scoped packet with
the same packet plus this neutral catalog. It asks whether exposing provenance
and time precision is enough. If it is not, the next comparison is a declared
vocabulary policy—not more graph traversal.

That neutral comparison completed with strict answer/source correctness of
3/8 in both arms. The catalog consumed 310,182 input tokens versus 260,212 for
the scoped packet (+19.2%). It changed period ordering from end to start in the
known collision, but still chose the wrong event family. It also failed to
exclude future events and could not recover the missing-Procedure control.

The next arm is therefore an intentionally benchmark-specific, burned-dev
vocabulary control. It declares that this benchmark's generic “procedure”
questions mean the inpatient ICD-coded family, applies the authoritative upper
time bound, and orders by event start. If that fixes the cases, it is evidence
for explicit query semantics—not evidence that more graph traversal helped.

## Current interpretation

Generic outbound closure is not the useful intervention on this benchmark. The
promising context-compiler primitives are:

1. explicit encounter-family scoping with declared temporal semantics;
2. complete inverse Encounter-to-event retrieval with absence receipts;
3. a provenance-aware clinical event catalog that separates coded procedures
   from bedside procedure events and defines a canonical timestamp per family.

The next answer probe is the eight-question neutral event-catalog comparison
above. A separate burned probe can test the 23 snapshot-open cases, keeping
empty results distinct from zero/false aggregates. Generic outbound closure has
already failed its mechanism test. A native graph database is still neither
required nor supported by this probe.
