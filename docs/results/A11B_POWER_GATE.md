# A11b prospective power gate

Status: **zero-model design gate implemented and adversarially clean; external
exact-head approval and the untouched corpus still pending**.

Date: 2026-07-15

Model and judge calls: **0**

## Frozen design

The committed `a11b-power-spec-v1` registers the primary E1-minus-T1 event-group
contrast and secondary T1-minus-T0 aids contrast before any A11b efficacy
patient identifiers, packets, gold, or answers exist. It uses:

- two-sided exact conditional McNemar power;
- familywise alpha 0.05 with a fixed 0.025/0.025 split;
- a minimum worthwhile effect of +10 percentage points for each contrast;
- 90% target power;
- a registered discordance grid from 10% through 30%;
- exactly one efficacy question per patient; and
- rounding to the next complete 16-patient balance block.

The exact calculation selects **384 unique efficacy patients**. The design also
reserves a patient-disjoint **64-patient development split**, so the next pinned
source generation must contain exactly **448 eligible synthetic Patients**.
At the limiting 30% discordance cell, 384 pairs have power
`0.903177057513`; the preceding balanced candidate, 368, has power
`0.888803130535` and therefore fails.

## Important assumption

The 30% discordance ceiling is a preregistered design assumption, not a theorem.
It is deliberately conservative relative to the sealed prior A11 E-minus-T
result, which had one discordant pair among 120, and the A11b E1/T1 arms still
share identical clinical evidence and temporal aids. The spec binds hashes of
that prior result document and manifest plus the observed 1/120 count.

If the true A11b correctness discordance exceeds 30%, this power claim does not
apply. The team must expand the nuisance grid and accept the larger derived
sample before opening efficacy artifacts; it may not reinterpret 384 as powered
after seeing results.

## Receipt and hardening

The deterministic receipt binds the complete design spec, every nuisance cell,
the unrounded selection calculation, the selected/predecessor power, the
balance rule, and hashes/byte counts of every executable dependency. It records
`efficacy_artifacts_opened: false` and `model_calls: 0`.

Adversarial tests cover forbidden efficacy/gold/packet inputs, patient reuse,
alpha and multiplicity drift, effect/discordance/balance drift, tampered
dependency receipts, rounded-threshold errors, insufficient search bounds, and
the 1,024-pair numerical boundary. The exact conditional implementation also
matches a separately enumerated multinomial calculation to floating-point
precision on a small case.

Committed artifacts:

- `fixtures/a11b_power_spec.json` — SHA-256
  `f0bafbc9b7bb7dc292f1a1142cf89ca492809187476a81dc35b8d4d2b06e815e`
- `docs/results/a11b-power-receipt.json` — SHA-256
  `3e249e82dd269a330e43ea344aed457bb71415408a359e65596a3e8a338dc1a4`

## What remains blocked

This gate does not create the efficacy corpus or authorize a model call. The
next slice must independently approve this spec/receipt, pin a new Synthea
release and generator commit, JAR and Java runtime hashes, seed, population,
configuration/modules, exporter settings, locale, timezone, reference date,
and ordered raw-output hashes. Only then may a deterministic builder assign
the 64/384 patient-disjoint splits and prove byte-identical clean-room builds.

## Reproduction

```bash
python3 a11b_power_gate.py compile \
  --spec fixtures/a11b_power_spec.json \
  --output /tmp/a11b-power-receipt.json
python3 a11b_power_gate.py verify \
  --spec fixtures/a11b_power_spec.json \
  --receipt /tmp/a11b-power-receipt.json
python3 -m unittest tests.test_a11b_power_gate -q
```
