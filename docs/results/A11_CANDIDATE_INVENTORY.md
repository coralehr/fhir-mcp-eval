# A11 sealed candidate-inventory result

Status: **complete zero-model feasibility audit; efficacy dataset not viable
on the current QT-4 packet snapshot**

Completed: 2026-07-14

## Sealed source

The aggregate-only scanner replayed every traversal receipt in the sealed
QT-4 treatment packets against its declared source JSON path, verified that
fetched targets and requested exact versions were present, and emitted no
question, patient, or resource identifiers.

- Source: `qt4-valid374-packets-0713/qt4t.jsonl`
- Source SHA-256:
  `39bdcac278c0fcb72f78e32cc219a391033093b6e3d108e22621e64bb4ed57af`
- Sealed rows: 374
- Dispatched microbiology rows: 44
- Unique dispatched patients: 33
- Rejected path receipts after replay: 0

The raw packet file is a sealed, gitignored experiment artifact on the Mac
mini at
`/Users/cory/fhir-mcp-eval-qt4-0713/runs/qt4-valid374-packets-0713/qt4t.jsonl`;
it is not distributed in a fresh clone. Reproduction therefore requires a
copy whose SHA-256 matches the receipt above.

## Inventory

| Measure | Count |
|---|---:|
| Rows with any fetched reference target | 22 |
| Rows with a fetched target at depth two | 10 |
| Rows with multiple clinically timed roots | 34 |
| Fetched path receipts | 159 |
| Missing path receipts | 460 |
| Target-cap receipts | 148 |

Only one replay-valid two-hop family appears:
`Observation.hasMember -> Observation.hasMember` (87 path instances across
the ten depth-two rows). The packet snapshot contains no second usable
two-hop family. A separate authenticated, read-only live-store count on
2026-07-14 observed zero `DiagnosticReport` and zero `Specimen` resources,
but that response was not bound to the sealed packet snapshot or a committed
response hash and is therefore supplemental only; it is not used for the
formal decision below.

## Decision

Do **not** run the preregistered 120-question V/T/E efficacy experiment on
this snapshot. The single available two-hop family conclusively fails the required four-
family breadth gate. The ten depth-two rows and 34 multi-root rows are also
strong warnings about sample and temporal breadth, but do not themselves
bound how many distinct questions a deterministic extractor could derive.
The scanner does not decide whether a future extraction could satisfy the
120-question, 60-temporal-question, patient-disjoint, or unanswerable strata.
Running anyway would turn the mechanism fixture into the claim and invite
selection bias.

The next valid choices are:

1. Add or select a second non-PHI FHIR substrate with real multi-hop
   `DiagnosticReport`, `Specimen`, encounter/location, medication, and
   provenance paths, then run the frozen deterministic extractor.
2. Audit the ten depth-two candidate rows and, if eligible, seal a clearly
   labeled microbiology micro-pilot to debug the product-packet adapter,
   answer prompt, and grading only. It cannot confirm efficacy or generality.
3. Amend the efficacy preregistration before any answer is generated if the
   research question is intentionally narrowed. Publish the amendment and
   preserve the current protocol.

The recommended route is option 1. The synthetic mechanism core is ready;
the remaining gates are the sealed product-packet adapter and dataset
topology, not more generic traversal code.

## Subsequent local substrate audit

A later zero-model local audit found a tracked 7,883-resource synthetic
HolyFHIR export with six frozen representative generic depth-two/three graph
families. It
does not reverse this decision: an exact scan found zero populated depth-two
paths under A11's four frozen microbiology families, and the export contains
only 11 patient clusters. It is suitable for a generic extractor pilot, not
the preregistered efficacy claim. See
[`A11_SUBSTRATE_AUDIT.md`](A11_SUBSTRATE_AUDIT.md).

## Reproduction

```bash
python a11_candidate_inventory.py \
  --packets /path/to/sha256-verified/qt4t.jsonl
```
