# A11 deterministic dataset gate

Status: **passed and sealed before answer calls; controller seal pending**

Date: 2026-07-15

Model and judge calls: **0**

## Seal result

The parent built the pinned official Synthea archive twice in independent
output directories on 2026-07-15. `diff -rq` reported no differences, both
verification passes accepted the independently supplied manifest hash, and
both manifests had SHA-256:

`442ca8d204fbd81f06e0abaf2ea5022b375deabb71a93d5ebaeccef98e99fe3c`

The canonical provenance receipt had SHA-256:

`e93fab76c1320d1a7156ea457a8b9c0e94accae70c6a75134c670bbe5c0a9f1e`

The zero-model audit passed all checks over 144 questions: 24 development and
120 efficacy, with 15 and 100 patient-disjoint Patients respectively. The
efficacy split has 15 rows in each of the eight family-depth cells, 60 first
and 60 latest questions, and 24 unanswerable questions split six each across
missing, stale-version, out-of-scope, and bound-exhaustion conditions. Every
failure mode has three first and three latest rows, every nonselected temporal
event has a complete path to a distinct competing fact, and the audit verifies
the actual failure mechanism independently of its gold label.

The complete producer-to-governance preflight also passed all 144 rows with
zero model calls. It executed the real `compile_evidence.py` producer shape,
strict packet adapter, pinned policy and source snapshot, shared immutable T/E
retrieval receipt, and question-bound T/E model payload APIs. Its artifact
SHA-256 is:

`4f630663a8e6517c1830cf5dc18858d3e174ab2c6321690d31c6d6deafae6ecb`

## What is implemented

`a11_dataset_builder.py` accepts the pinned official Synthea sample-data ZIP
directly, a strict provenance JSON receipt, and an output directory. It never
downloads or invokes Synthea. It produces byte-deterministic, separated
artifacts for the source snapshot, augmented source corpus, ordered questions,
gold, canonical policy contexts, question order, zero-model audit, and governed
producer preflight, plus a manifest and manifest-hash sidecar.

The official-source receipt carries manually pinned repository and generator
commit metadata, recomputes the archive Git blob SHA-1, and binds archive
SHA-256 and bytes, ordered ZIP entries with per-entry hashes
and byte counts, selected JSON-content hash, augmentation seed, frozen profile,
and relevant compiler dependency hashes. It contains no wall-clock timestamp
or absolute machine path.

The frozen profile uses all 115 source Patients after deterministic hash order:
15 development and 100 efficacy, patient-disjoint. It generates 24 development
and 120 efficacy questions balanced across the four registered families and
depths two/three. The efficacy partition freezes 15 rows per family-depth cell,
60 first and 60 latest questions, and 24 unanswerable rows: three per cell and
six per registered failure mode.

The zero-model audit rejects duplicate IDs/questions, split overlap, quota
drift, missing versions, non-replayable paths, unregistered edges, practice or
patient leakage, forbidden label/gold keys in source metadata, terminal/root
aliases in V, V star-answerability, ambiguous depth wording, missing or
duplicate terminal routes, direct/alternate shorter paths, wrong normalized-
UTC root rank, incomplete temporal competitors, temporally confounded failure
modes, mislabeled failure mechanisms, artifact tampering, dependency drift,
and nondeterministic rebuilds.

## What a passing seal proves

A passing parent-side build proves that the preregistered A11 topology corpus
can be reproduced without hand-picking favorable rows, that each eligible
answerable terminal is absent from V and reachable through exactly one frozen
registered path at the declared depth, and that the registered unanswerable
mechanisms fail closed. It proves dataset and compiler mechanics only.

## What it does not prove

It does not prove graph traversal or event grouping improves answer accuracy,
that a native graph database is preferable to Postgres, or that production
authorization is complete. It licenses assembly of the separately sealed A11
controller. Answer calls remain blocked until that controller independently
binds the dataset hash, model, Codex version, prompts, answer schema, retry
policy, panel, grading configuration, and fixed analysis order. Once that seal
exists, no answers may be inspected mid-run and no sealed inputs may change.

## Reproduction

```bash
python3 a11_dataset_builder.py write-official-sample-provenance \
  --input synthea_sample_data_fhir_latest.zip \
  --output a11-source-provenance.json
python3 a11_dataset_builder.py build \
  --input synthea_sample_data_fhir_latest.zip \
  --provenance a11-source-provenance.json \
  --output-dir a11-dataset-seal
python3 a11_dataset_builder.py verify \
  --output-dir a11-dataset-seal \
  --expected-manifest-sha256 \
  442ca8d204fbd81f06e0abaf2ea5022b375deabb71a93d5ebaeccef98e99fe3c
```
