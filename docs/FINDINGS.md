# Findings — start here

This is the short capstone and document map for the FHIR-agent experiments in
this fork. The substrate for the original program was FHIR-AgentBench's
MIMIC-IV-on-FHIR demo on a self-hosted Medplum R4 server. QT-4 and A11 added
stricter corpus separation, preregistration, and sealed-run controls. A11b r3
was an explicitly unregistered exploratory run. Each canonical result records
its own dataset and governance boundary.

## What we know

The first-order constraint in the original 409-question study was whether the
question-relevant clinical data reached the model without overflowing its
context. Raw FHIR overflowed the 32k window on 262/409 questions. A code path
performed better in the pooled comparison because it routed payloads outside
the prompt; where both agents returned real answers, it showed no significant
reasoning advantage. A blunt, query-blind projection recovered only part of the
overflow loss. The defensible product direction is therefore bounded,
query-aware context selection—not “more tools” or a graph database by itself.

The tool-catalog, payload-coaching, and higher-reasoning-effort experiments did
not produce a reliable accuracy gain. The tool-catalog study was underpowered
for small effects, and the Opus run's raw artifacts were lost with its ephemeral
EC2 environment, so its cap result remains credible but unreproducible. The
GPT-5.5 side is preserved.

The grading audit also found that our configured gpt-5-mini judge was unreliable
on the deterministic numeric slice and biased toward false negatives. A
deterministic-first grader plus independent three-vote panels produced stable
labels and exposed a boolean-grading bug in our own first repair. This is a
finding about the measured configuration—not the unmeasured benchmark-default
o4-mini judge.

The later graph/context experiments sharpened the claim:

- QT-4 promoted fixed, question-only terminology selection. Generic traversal
  increased evidence recall, but its incremental correctness contrast missed
  the registered promotion gates.
- A11 proved bounded traversal can solve deliberately path-required questions.
  Event grouping added only one correctness flip over traversal and bundled
  several features, so it earned no independent causal claim.
- The A11b r3 exploratory artifacts tied after normalization, but that
  normalization erased structured insufficiency reasons. The preview supports
  a fresh prospective component screen; it does not support promotion.

## Canonical document ownership

Each result now has one canonical home. Sibling documents link to it instead of
repeating the same headline tables.

- [FINAL_REPORT.md](FINAL_REPORT.md) is the **canonical full report** for the
  original A0/A0-prime/A5 three-arm result, exact statistics, caveats, and
  projection decomposition.
- [TRUSTWORTHY_REGRADE.md](TRUSTWORTHY_REGRADE.md) is the **canonical grading
  audit** and owns the judge-reliability tables, defect history, and final label
  construction.
- [REPORT.md](REPORT.md) is the **tool-ablation deep dive**: generic versus typed
  tools, context-cap factorial, paired statistics, and reproducibility limits.
- [CODE_EXPERIMENT.md](CODE_EXPERIMENT.md) is the **mechanism note** for the code
  interpreter and its payload-routing confound.
- [results/QT4_VALID374_RESULT.md](results/QT4_VALID374_RESULT.md) and
  [results/QT4_VALID374_FORENSIC_AUDIT.md](results/QT4_VALID374_FORENSIC_AUDIT.md)
  own the confirmatory terminology/traversal result and audit.
- [results/A11_RESULT.md](results/A11_RESULT.md) and
  [results/A11_FORENSIC_AUDIT.md](results/A11_FORENSIC_AUDIT.md) own the V/T/E
  path-required result and forensic review.
- [results/A11B_R3_FORENSIC_AMENDMENT.md](results/A11B_R3_FORENSIC_AMENDMENT.md)
  is the required interpretation layer for the preserved
  [A11b r3 exploratory result](results/A11B_R3_UNREGISTERED_EXPLORATORY_RESULT.md).

## What is not yet proven

We have not shown that event grouping independently improves accuracy, that a
native graph store beats a governed relational projection, or that one model's
result generalizes across APIs. The next confirmatory experiment must isolate
the temporal-rank, selected-marker, answerability-receipt, and representation
components on development data, freeze one winner, and test it once on a fresh
patient-disjoint efficacy split. Cross-API hardness marks come only after that
exact contrast and grader are sealed.

## Reproducibility boundary

The original trustworthy aggregate and per-question answer backup are committed
under `medplum-eval/`; some raw answer dumps and per-question panel labels remain
gitignored and require regeneration plus panel calls. The QT-4 and A11 result
directories preserve their registered result and audit artifacts. Treat every
document's stated artifact boundary as part of the claim, not as a footnote.
