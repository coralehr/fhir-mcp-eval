# A11 four-family producer gate

Status: **passed before answer calls; efficacy still blocked**

Completed: 2026-07-14

## Decision

Keep the untouched QT-4 product recipe frozen and add the separately versioned
`a11-four-family-v1` recipe for A11. This resolves the mechanical inability to
produce DiagnosticReport-root packets without treating the change as a QT-4
promotion or silently changing historical packet behavior.

## Zero-model result

The public producer and planner interfaces now reach exactly four registered
question/path families:

1. `Observation.hasMember -> Observation.hasMember`
2. `Observation.hasMember -> Observation.specimen`
3. `DiagnosticReport.result -> Observation.hasMember`
4. `DiagnosticReport.result -> Observation.specimen`

The actual `compile_evidence.py` entrypoint was exercised against a local
synthetic non-PHI FHIR client. Its non-plan-only JSONL and manifest were then
loaded by `a11_packet_adapter.py` v2 under an independently pinned manifest
SHA-256. The adapter re-derived the recipe-bound intent, search plan and A11
question plan; recomputed packet and file hashes; and returned the expected
patient-consistent Observation and DiagnosticReport roots. No answer or judge
model was called.

The negative gate rejects recipe-confused bundles, altered A11 protocol,
status, feature or planner receipts, and benchmark-only keys nested inside
otherwise valid clinical resources even when the attacker recomputes packet
and manifest hashes. It also rejects ambiguous multi-Patient absolute
references and DiagnosticReport roots that would require publication time as
a clinical clock. DiagnosticReport root selection is locally ordered from
`effective[x]` only and does not use server `date` sorting or date windows.

Historical isolation is tested explicitly: `qt4-vocabulary-promoted-v1`
continues to bind `qo-v2.1`, emit Observation-only microbiology searches and
use `a11-question-plan-v1`. The new A11 route binds
`qo-v2.2-a11-four-family` and `a11-question-plan-v2-four-family` only when the
new recipe is selected.

## What remains blocked

This gate proves producer reachability, version isolation and packet
integrity. It does not prove that any arm answers correctly, that traversal or
event grouping helps, or that a graph database is superior. Before V/T/E may
run, the program still needs:

- a governed principal/practice/purpose/patient/source-version receipt that
  derives authorized roots from the sealed V packet;
- one immutable version-preserving retrieval result shared by T and E; and
- a deterministically generated, sealed non-PHI corpus with at least 120
  questions, four families, patient-disjoint partitions and the registered
  unanswerable conditions.

## Reproduction

```bash
python3 -m unittest \
  tests.test_a11_four_family_recipe \
  tests.test_a11_packet_adapter \
  tests.test_a11_event_group_benchmark
```

The binding protocol amendment is
[`A11_EVENT_GROUP.md`](../prereg/A11_EVENT_GROUP.md). The remaining dataset
gap is documented in [`A11_SUBSTRATE_AUDIT.md`](A11_SUBSTRATE_AUDIT.md).
