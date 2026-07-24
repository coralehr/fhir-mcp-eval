# A0/A0-prime/A5 reproducibility package

This package is the minimized, answer-level scoring input for the historical
409-question comparison. It contains correctness labels, patient clusters,
stratum membership, A0-prime grade provenance, and residual-overflow flags. It
does not contain prompts, traces, FHIR payloads, or full model answers.

The source data is the de-identified MIMIC-IV-on-FHIR demo described in
[`NOTICE-DATA.md`](../../NOTICE-DATA.md). It is not clinical data and is not for
clinical use.

Reproducibility boundary: the committed score artifact independently reproduces
the Git-scoped score-to-table computation, while rebuilding raw runs into that
artifact is only hash-verifiable for holders of the 295 MB gitignored inputs.

## Committed artifacts

- `score-artifact.json`: 409 minimized question records plus SHA-256 receipts
  for every raw answer, label, stratum, and panel-vote input used to build it.
  SHA-256: `b0bc19c605aea20ada713613ee1f8d1e1bfb1d814f6bba38a4e77637b3ddc242`.
- `cluster-stats.json`: deterministic 100,000-replicate patient-cluster
  intervals and exact McNemar summaries for the matched stratum. SHA-256:
  `b1283f87056ee3a16a1ad333b8a39a1c25248a8f0e113a70352b8e7410085e85`.
- `failure-decomposition.json`: qid-level primary outcomes and deterministic
  cap-drop, earliest/first, repeated-resource, code-recovery, and pinned
  `cl100k_base` single-tool-block token receipts. SHA-256:
  `ad0a90847f95d541510c37602afd23d05dadd83963bda724b7a50c28a8ae206c`.
- `failure-decomposition.md`: generated human-readable summary and category
  definitions. SHA-256:
  `e1aaf237d3d513244ee0f2ff9bab4ad19adf18955c32d78fc5e0fc777cee607c`.

The three external raw answer dumps are 295 MB combined. Their individual
byte counts and SHA-256 checksums, plus the receipts for all grading inputs,
are embedded in `score-artifact.json` under `source_receipt.files`.

## Recompute from a fresh checkout

No raw run directory, model key, judge call, or network access is required:

```bash
python3 a0prime_verdict.py
python3 a0prime_cluster_stats.py --out /tmp/a0prime-cluster-stats.json
cmp artifacts/a0prime-v1/cluster-stats.json /tmp/a0prime-cluster-stats.json
python3 decompose_a0prime_failures.py \
  --json-out /tmp/a0prime-failure-decomposition.json \
  --markdown-out /tmp/a0prime-failure-decomposition.md
cmp artifacts/a0prime-v1/failure-decomposition.json /tmp/a0prime-failure-decomposition.json
cmp artifacts/a0prime-v1/failure-decomposition.md /tmp/a0prime-failure-decomposition.md
```

## Rebuild from the original raw runs

If the gitignored historical `runs/full409` and `runs/a0prime` directories are
available in another checkout, regenerate and compare the minimized artifact:

```bash
python3 build_a0prime_artifact.py \
  --source-root /path/to/FHIR-AgentBench \
  --out /tmp/a0prime-score-artifact.json
cmp artifacts/a0prime-v1/score-artifact.json /tmp/a0prime-score-artifact.json
```

`build_a0prime_artifact.py` is the only script in this package that requires
the historical A0/A0-prime/A5 raw answers. The older exploratory scripts
`build_labels.py`, `codex_judge_compare.py`, `final_grade.py`,
`judge_leaderboard.py`, `magnitude_analysis.py`, and `rescore_canonical.py`
also read `runs/full409`; `rejudge_409.py` additionally performs live judge
calls for missing labels. They are not needed to reproduce the final table,
patient-cluster intervals, or failure decomposition.
