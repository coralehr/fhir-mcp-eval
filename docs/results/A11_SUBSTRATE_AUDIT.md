# A11 non-PHI substrate audit

Status: **complete read-only local audit; no answer or judge model calls**

Completed: 2026-07-14

Update: the separately versioned `a11-four-family-v1` producer amendment now
resolves the DiagnosticReport reachability blocker described by this audit,
without changing historical `qo-v2.1`. The topology and patient-cluster gaps
below remain unchanged, so the efficacy run is still blocked. See
[`A11_FOUR_FAMILY_PRODUCER_GATE.md`](A11_FOUR_FAMILY_PRODUCER_GATE.md).

## Decision

No currently available local corpus is eligible for the frozen A11 efficacy
protocol. One tracked synthetic export is large and graph-rich enough to
exercise a generic path extractor, but it has zero depth-two paths under
A11's registered microbiology relations. At audit time, the frozen `qo-v2.1`
product planner also never queried `DiagnosticReport`; that separate producer
blocker is now resolved by the dated versioned amendment. The preregistered
answer run remains blocked on dataset topology and the governed authorization/
source-version receipt.

## Strongest immediate seed: HolyFHIR synthetic bulk export

The best local, redistributable seed is:

`development/sample-bulk-fhir-datasets-10-patients.zip` from the tracked
HolyFHIR Personal Family EMR checkout.

- Source repository commit:
  `06d9d7e4d1abf39b8639b0911e5d63b0006f6ff5`
- Archive SHA-256:
  `a9eba7c2b15ab2e027b633584669fc0ae08808056c1caddb3825e04764006bd7`
- License: MIT
- Data policy: the repository requires synthetic-only sample data and the
  archive contains Synthea markers, not MIMIC markers.
- Total resources: 7,883
- Patient resources: 11
- Resource types: 18
- DiagnosticReport: 780
- Observation: 2,850
- Specimen: 413
- ServiceRequest: 413
- Encounter: 413
- Procedure: 1,341

The committed aggregate-only scanner froze six representative populated
generic depth-two or depth-three families:

| Generic path family | Roots | Paths | Patient clusters |
|---|---:|---:|---:|
| DiagnosticReport.result -> Observation.encounter -> Encounter.episodeOfCare | 367 | 1,684 | 11 |
| Procedure.reasonReference -> Condition.encounter -> Encounter.episodeOfCare | 620 | 620 | 11 |
| DocumentReference.context.encounter -> Encounter.episodeOfCare | 413 | 413 | 11 |
| ServiceRequest.encounter -> Encounter.episodeOfCare | 413 | 413 | 11 |
| Observation.encounter -> Encounter.episodeOfCare | 2,850 | 2,850 | 11 |
| MedicationRequest.reasonReference -> Condition.encounter | 85 | 85 | 10 |

This makes the archive useful for freezing a generic extractor, path replay,
redaction, packet-equivalence, and receipt machinery.

It does **not** satisfy the current A11 relation registry. A separate exact
scan found no populated depth-two instance of:

- `Observation.hasMember -> Observation.hasMember`;
- `Observation.hasMember -> Observation.specimen`;
- `DiagnosticReport.result -> Observation.hasMember`; or
- `DiagnosticReport.result -> Observation.specimen`.

It also has only 11 patient clusters, which is too small for persuasive
patient-cluster bootstrap inference even if the relation mismatch were fixed.
It may support a clearly labeled mechanism or extractor pilot, not the
confirmatory efficacy claim.

## Other inspected sources

- The current QT-4 snapshot remains too narrow: ten depth-two rows and one
  registered family. Its sealed result remains in
  [`A11_CANDIDATE_INVENTORY.md`](A11_CANDIDATE_INVENTORY.md).
- Bonfire's committed synthetic graph fixtures contain only 24 resources
  across three patients. They exercise generic encounter paths but do not
  supply A11 breadth.
- A local patient-history bundle is topology-rich but represents one patient
  and lacks a clear local license/provenance receipt. It is ineligible.
- The reproducible MIMIC-IV-on-FHIR demo loader has 100 de-identified patients
  but deliberately excludes Specimen and does not supply four registered A11
  families. It also carries ODbL attribution obligations.
- The existing deterministic Synthea fixture generator has population and
  seed controls, but its Synthea release/JAR hash is not pinned and no generated
  efficacy corpus is currently sealed.

## Recommended construction

1. Publish a pre-answer amendment that either versions a microbiology
   `DiagnosticReport` query into the product planner/recipe or changes the
   four-family requirement. Re-run the zero-model producer/adapter byte gate;
   do not infer roots from gold metadata or hand-authored plans.
2. Pin a Synthea release, JAR SHA-256, seed, population, configuration, and raw
   output hash.
3. Apply a committed deterministic graph augmenter that emits the amended,
   producer-reachable A11 families, depth-three variants, multiple dated roots,
   and registered unanswerable mutations.
4. Generate at least 100 patients, partition by patient before question
   selection, and select deterministically by family and hash order.
5. Validate FHIR, duplicate leakage, label leakage, root answerability, path
   replay, scope, exact versions, and all packet bounds before sealing.
6. Use the HolyFHIR archive as an independently authored generic-graph pilot,
   not as evidence for the designed microbiology graph.

If the research question is broadened to the generic HolyFHIR paths instead,
publish a preregistration amendment first and preserve the current protocol.
Do not inspect answer content while deciding which route to take.

## Reproduction

The aggregate-only scanner emits no resource or patient identifiers. Its
machine-readable output is committed as
[`A11_SUBSTRATE_AUDIT_RECEIPT.json`](A11_SUBSTRATE_AUDIT_RECEIPT.json).

```bash
python a11_substrate_audit.py \
  /path/to/sample-bulk-fhir-datasets-10-patients.zip \
  --source-url https://github.com/michaelbdavidson7/holyfhir-personal-family-emr.git \
  --source-commit 06d9d7e4d1abf39b8639b0911e5d63b0006f6ff5
```

The receipt binds the archive, source commit, scanner, resource counts, six
generic families, and all four registered A11 families.
