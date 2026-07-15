# A11 deterministic dataset gate

Status: **implemented pre-answer; pending parent-side seal verification**

Date: 2026-07-15

Model and judge calls: **0**

## What is implemented

`a11_dataset_builder.py` accepts the pinned official Synthea sample-data ZIP
directly, a strict provenance JSON receipt, and an output directory. It never
downloads or invokes Synthea. It produces byte-deterministic, separated
artifacts for the source snapshot, augmented source corpus, ordered questions,
gold, canonical policy contexts, question order, and zero-model audit, plus a manifest and manifest-hash
sidecar.

The official-source receipt binds repository and generator commits, archive
Git blob, archive SHA-256 and bytes, ordered ZIP entries with per-entry hashes
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
UTC root rank, artifact tampering, dependency drift, and nondeterministic
rebuilds.

## What a passing seal proves

A passing parent-side build proves that the preregistered A11 topology corpus
can be reproduced without hand-picking favorable rows, that each eligible
answerable terminal is absent from V and reachable through exactly one frozen
registered path at the declared depth, and that the registered unanswerable
mechanisms fail closed. It proves dataset and compiler mechanics only.

## What it does not prove

It does not prove graph traversal or event grouping improves answer accuracy,
that a native graph database is preferable to Postgres, or that production
authorization is complete. No answer-bearing run is licensed until the parent
records a successful double-build seal and the separately governed principal,
practice, purpose, patient, immutable source-version, and shared T/E retrieval
receipt gate is closed.
