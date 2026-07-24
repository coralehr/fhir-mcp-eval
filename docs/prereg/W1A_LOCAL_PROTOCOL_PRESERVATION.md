# W1A local protocol preservation note

> Evidence status: preserved after the run, not an independently Git-anchored pre-registration.
> The local file was born at 2026-07-22T01:17:24-07:00, before the corresponding answer run,
> but it was modified at 2026-07-22T14:19:26-07:00, after results existed. Its preserved
> SHA-256 is `5017b32093110f0354f444f146dcc210889d325b94067dee9f5e46ac76bafa11`. The text below is useful for reconstructing intent,
> but it cannot by itself prove that every detail was frozen before observation.

---

# PRE-REGISTRATION — W1-A: deterministic pre-join packet on the measured tier

Registered 2026-07-22, BEFORE any test-set inference. Frozen once the dev phase ends.

## Hypothesis

A deterministic **pre-join engine** — which (1) detects when a question references a
specific encounter ("last hospital visit", "current visit", visit counts), (2) fetches and
deterministically orders the patient's Encounters, (3) selects the referenced encounter,
and (4) scopes event retrieval to that encounter's period — will beat the A6a selection
packet on the 409-question measured test set, because the exploratory tier showed the
join is the binding failure (E15: ORACLE-join 0.967 vs FOCUS 0.567; neither the agent nor
a flat packet can express "events WHERE encounter = X").

## Census basis (W1-D, run 2026-07-22, deterministic from gold SQL — analysis only)

- n=409 test. Join class: 84.4% encounter-join, 15.2% single-hop, 0.5% multi-join.
- **Link semantics: 176/409 (43.0%) specific-encounter** (the join selects a particular
  visit) vs 226 (55.3%) patient-scope-only (the join merely scopes to the patient — the
  chart-scoped packet already collapses it) vs 7 no-link.
- 92.2% temporal, 40.1% aggregate, 44.5% need a dimension (code) lookup.
- Artifacts: `fhir-mcp-eval/census_w1d.py`, `census_w1d_tags.json`, `census_w1d_report.txt`
  (laptop-side only; tags never shown to any agent).

## Arms

| arm | description |
|---|---|
| A6a (baseline) | frozen selection packet, question-only planner (existing, 54.3% test) |
| **W1A pre-join** | A6a planner + encounter-selection + encounter-scoped retrieval; same reader, same prompt scaffold, same bounds |

Same model, same prompt template, same token bounds, same judge/scoring path as the A6a
confirmatory run. The ONLY difference is the packet construction.

## Endpoints

- **Primary:** pooled accuracy on the 409 test questions, W1A vs A6a, paired per-question.
  Promotion requires McNemar p < 0.05 AND patient-clustered bootstrap 95% CI excluding 0.
- **Secondary (mechanism check):** accuracy difference within the 176 specific-encounter
  questions. Prediction: the effect concentrates here.
- **Falsifier:** within the 226 patient-scope-only questions we predict ≈ no difference.
  If W1A wins there but not in the specific-encounter subgroup, the mechanism story is
  wrong even if the pooled number is up.
- Also reported (not gates): payload bytes/tokens, packet-build determinism (2× rebuild
  hash-identical), overflow count, $ cost.

## Integrity rules (carried over from the A6a confirmatory design)

- Builder reads ONLY `QUESTION_ONLY_FIELDS` (split, question_id, question, assumption,
  patient_fhir_id). No gold fields; whitelist enforced in code.
- Census tags are stratification metadata computed from gold SQL — used ONLY in analysis
  after answers are frozen; never visible to the builder or reader.
- Dev on train/valid split (≤60 valid questions); FREEZE the builder (git commit hash
  recorded here) before the single test-409 run. No test-set iteration.
- Runs on the mini in the jailed cwd; scoring off-host on the laptop against the gold CSV.

## Freeze record (fill before test run)

- FROZEN 2026-07-22 after dev iteration v1→v4 (detector patches, family join,
  facility-aware selection, scoped relaxation). Freeze is by file hash (the
  eval repo has another session's merge in flight; not entangling):
  - `w1a_prejoin_builder.py` sha256 `6fd13dd598dbe9b9c4ff415b5d18e7de27a92d7295622a526e2143eaec33dfc1`
  - `a6_packet_builder.py` sha256 `7ec7582ae5f96e16e7bad77af0720b649f8ada16b1c0b9a15afc8010cbd49ef9` (unchanged baseline)
- Dev-split result at freeze (ROUGH lower-bound scorer, dev-only, NOT a
  finding): W1A-v4 25/60 vs A6a 20/60; specific-encounter 13/30 vs 9/30;
  flips +5/−0. Dev artifacts: runs/codex-w1a-dev60-v4, runs/codex-a6a-dev60,
  w1a_dev_compare.py.
- Contemporaneity note: the stored A6a test-409 answers are from 2026-07-11
  (different codex CLI version). For a valid paired claim the test run MUST
  re-run BOTH arms contemporaneously (interleaved chunks, same CLI/model),
  not pair fresh W1A against the stored A6a run.
- test run date: 2026-07-22 → 2026-07-23 (contemporaneous dual-arm, 409+409,
  interleaved chunks, codex-cli 0.144.1; 2 transient stragglers swept in mop-up)

## RESULT (canonical grading: deterministic 128 + arm-blind 3-vote panel 550)

- **PRIMARY pooled (n=409): W1A 56.0% vs A6a 54.0% = +2.0pp — NOT PROMOTED**
  (discordant +23/−15, McNemar p=0.256, cluster-bootstrap 95% CI [−0.9, +4.5]pp).
- **SECONDARY specific-encounter (n=176): W1A 68.2% vs A6a 61.4% = +6.8pp —
  significant** (discordant +15/−3, McNemar p=0.0075, CI [+1.8, +12.4]pp).
- **FALSIFIER patient-scope-only (n=226): −1.8pp, p=0.50 — no effect, as
  predicted.** The mechanism story survives its own falsifier.
- Payload: W1A total 26.7M chars vs A6a 41.4M (−36%); triggered subset median
  20k vs 35k chars (−43%) — the join packet is smaller AND better where it fires.
- Reproducibility: contemporaneous A6a rerun 54.0% vs 54.3% (2026-07-11).
- Honest headline: **the deterministic pre-join improves exactly and only the
  questions that need a join (+6.8pp on 43% of the benchmark, at −43% payload),
  is neutral elsewhere, and the pooled +2.0pp is diluted below significance.**
  The pooled claim is not earned; the mechanism claim is.
- Artifacts: runs/codex-w1a-test409, runs/codex-a6a-test409-w1apair,
  runs/w1a-confirmatory-grading/{det_verdicts,panel_verdicts,final_w1a,final_a6a}.json,
  w1a_final_stats.py output above.

## Cost estimate

One packet arm at n=409 ran ~$35 historically (A5) and selection arms cheaper (smaller
packets). Budget cap for the W1A test run: **$45**; dev phase ≤ $10.
