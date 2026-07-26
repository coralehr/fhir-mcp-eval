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

## Answer probes

The first medication smoke case (`1436276ff4d05d626c742fa4`) failed in both
the flat and outbound-closure packets. Both answered approximately 687.37
hours, while the benchmark answer is empty because no admission is active at
the authoritative time. Outbound reference traversal resolved Medication
names but did not solve the inverse join from the requested visit to its
events.

The first reverse-join implementation then exposed a selector defect: when no
Encounter contained the authoritative time, it fell back to the chart's final
Encounter, including future Encounters. The corrected inverse-Encounter scope
emits explicit negative evidence instead of selecting a non-current visit.

Seven additional Codex-subscription episodes compared flat, outbound closure,
and inverse Encounter scoping on three mechanism cases:

| Question | Flat | Outbound | Inverse Encounter |
|---|---|---|---|
| Named procedure in first visit | correct | correct | correct |
| No active stay / glucagon | wrong in prior smoke | wrong in prior smoke | correct negative evidence |
| Generic last procedure | wrong | wrong | wrong |

The inverse packet reduced input tokens substantially:

| Question | Flat input | Outbound input | Inverse input |
|---|---:|---:|---:|
| Named procedure | 89,901 | 93,803 | 46,249 |
| Generic last procedure | 23,096 | 26,578 | 18,891 |
| No active stay | 83,744 | 92,237 | 14,954 |

These are subscription usage receipts from one non-seeded episode per cell,
not API prices or independent replicates.

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

## Current interpretation

Generic outbound closure is not the useful intervention on this benchmark. The
promising context-compiler primitives are:

1. inverse Encounter-to-event scoping;
2. explicit negative evidence for empty graph neighborhoods;
3. a provenance-aware clinical event catalog that separates coded procedures
   from bedside procedure events and defines a canonical timestamp per family.

Next exploratory work should test those three primitives on the rest of the
burned visit-specific questions before any larger run. A native graph database
is still neither required nor supported by this probe.
