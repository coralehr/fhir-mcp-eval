# A0/A0-prime/A5 reproducibility package

This package is the minimized, answer-level scoring input for the historical
409-question comparison. It contains correctness labels, patient clusters,
stratum membership, A0-prime grade provenance, and residual-overflow flags. It
does not contain prompts, traces, FHIR payloads, or full model answers.

The source data is the de-identified MIMIC-IV-on-FHIR demo described in
[`NOTICE-DATA.md`](../../NOTICE-DATA.md). It is not clinical data and is not for
clinical use.

## Committed artifacts

- `score-artifact.json`: 409 minimized question records plus SHA-256 receipts
  for every raw answer, label, stratum, and panel-vote input used to build it.
  SHA-256: `39a545d9f5da0d2ec7559f7d699b9dee967c7996fb35bf9f771d71e7b9b35240`.
- `cluster-stats.json`: deterministic 100,000-replicate patient-cluster
  intervals and exact McNemar summaries for the matched stratum. SHA-256:
  `54354d9509966d660d39942a4528ad36db3bb9d7a24e769718b20151afc220ac`.

The three external raw answer dumps are 295 MB combined. Their individual
byte counts and SHA-256 checksums, plus the receipts for all grading inputs,
are embedded in `score-artifact.json` under `source_receipt.files`.

## Recompute from a fresh checkout

No raw run directory, model key, judge call, or network access is required:

```bash
python3 a0prime_verdict.py
python3 a0prime_cluster_stats.py --out /tmp/a0prime-cluster-stats.json
cmp artifacts/a0prime-v1/cluster-stats.json /tmp/a0prime-cluster-stats.json
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
calls for missing labels. They are not needed to reproduce the final table or
its patient-cluster intervals.
