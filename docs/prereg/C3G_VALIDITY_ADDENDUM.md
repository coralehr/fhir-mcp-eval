> Canonical copy imported from `ticvision/cstack` commit `bb58d51828486b181441d6e2c203f096bf2bf1f7`.
> Source SHA-256: `2b210b6224b28dcf91bf76c282d6edd0af4def280afe2a3ad1a65e4b952d9481`.

# PRE-REGISTRATION ADDENDUM — Arm 4 pre-run validity corrections

**Written 2026-07-21 before any G0/C1/C2/C3/A4/RAW arm in the 2026-07-21
program ran.** This is a prospective correction to
`PREREGISTRATION-arm4-graph-2026-07-21.md`, not a rewrite of it.

The original document remains byte-for-byte unchanged at commit
`e6b39a7c5af402517defcdb3a4a7da9430e88090`, published on remote branch
`codex/arm4-original-prereg-20260721`. This addendum supersedes only the
holdout, intervention, replicate, inference, judge, retry, metric, and power
language identified below.

## 1. Why this correction is required

Adversarial pre-run review found that:

1. The original commit did not contain or fully hash-bind the split, public
   input, runner, prompts, graph compiler, scorer, or analysis code.
2. All 79 proposed test question IDs already occur in earlier answer, gold,
   human-review, or result artifacts. They are not globally unseen.
3. Eighteen Patients overlap the 50-question dev set and the proposed test;
   27 of 79 test questions belong to those Patients.
4. The original A4 was `G0 + graph`, while its primary comparator C3 was
   `G0 + search-craft + empty-retry`. That contrast changed more than one
   treatment dimension and could not isolate marginal graph value.
5. The original precision implementation did not penalize off-gold-type
   resource access and could award vacuous perfect precision on empty-needed-set
   questions.
6. Three stochastic executions per question, multiple questions per Patient,
   and ordinary Wilson/McNemar inference were not reconciled.

No arm had run when these defects were found. Correcting them now preserves a
prospective experiment. Silently editing the original document would not.

## 2. The old 79 are burned

The previously named 79-question test is **development/forensic data only**.
It may not support a confirmatory, leakage-proof, promotion, “proved,” or
“killed” claim.

Before any confirmatory model call, create a new private holdout satisfying all
of these conditions:

1. Every question ID is absent from every prior answer, gold, human-review,
   score, and result artifact.
2. No `patient_fhir_id` appears in the burned 50-question dev set or the burned
   79-question set.
3. Sampling is grouped by Patient and stratified by template family and source
   table. Patient is the non-splittable grouping unit. Template-stratified does
   not mean template IDs must be disjoint.
4. The selection algorithm, fixed RNG seed, ordered public-metadata projection
   `(question_id, patient_fhir_id, template_id, main_table_name)`, and full
   SHA-256 digests are frozen before model calls.
5. Test gold, test question IDs, and per-question results remain off the solver
   host. The solver receives only the question scheduled for the current
   episode.
6. The test is released once, only after every arm, prompt, model version,
   retry policy, judge, schedule, scorer, and harness commit is frozen. Any
   per-question inspection burns the holdout.

The new holdout requires a second dated sealing receipt with its exact hashes,
aggregate strata counts, Patient count, power calculation, and executable
bundle manifest. **Until that receipt exists and passes preflight, the test
command must remain unavailable.**

## 3. Corrected intervention and estimand

The confirmatory graph arm is **C3G = C3 + graph**, not `G0 + graph`.

C3G and C3 must be identical in answer model and effort, base/system prompt,
FHIR search-craft guidance, empty-result recovery, model-call and round budget,
FHIR request budget, fetcher, answer path, truncation limits, timeout policy,
and operational retry policy. Their only permitted difference is the
deterministic, versioned graph-computed needed-set packet supplied before
planning.

Freeze the graph generator's allowed inputs, roots and root-selection rule,
edge vocabulary and direction, traversal bounds, ordering, serialization,
packet byte/token cap, path citations, source snapshot, and output hash before
test. It may not consume gold, result artifacts, question IDs as special cases,
or Patient-specific handwritten rules.

The primary estimand is the question-weighted single-execution effect:

```text
Delta_G = mean_q(mean_r(correct[C3G,q,r] - correct[C3,q,r]))
```

Patient clustering changes uncertainty, not the equal question weights in the
estimand. This estimates the effect of the complete graph-needed-set
intervention; it does not separately identify topology, serialization, or
context-volume effects. A `G0 + graph` arm, if run, is exploratory.

## 4. Replicates and execution schedule

“Seeds” are renamed **replicates** because the subscription CLI is not
seedable. Run exactly three independent accepted replicates for every
arm-question pair.

Freeze a counterbalanced, block-interleaved arm-order schedule before
execution. Do not pool the resulting `3N` outcomes as independent rows.
For each arm and question:

```text
question_score[a,q] = mean(correct[a,q,replicate 1..3])
cell_accuracy[a] = mean_q(question_score[a,q])
```

Report every replicate's accuracy and between-replicate standard deviation.
Majority correctness is secondary only; it describes a three-run policy rather
than normal single-run behavior.

## 5. Cluster-aware inference and registered family

Patient is the resampling and randomization cluster. Primary two-sided 95%
percentile intervals use a fixed-seed, 100,000-draw Patient-cluster bootstrap
that keeps all questions and replicates for a Patient together. The precision
gate uses a one-sided 95% cluster-bootstrap lower bound. Report both the
registered question-weighted estimate and a Patient-weighted sensitivity
estimate.

Primary contrast p-values use a null-imposed wild-cluster bootstrap-t with
Rademacher weights at Patient level, 99,999 fixed-seed draws, and a
cluster-robust studentized statistic. The exact statistic and finite-sample
correction must be frozen in analysis code before sealing. Paired arm-label
swaps may be reported as sensitivity analyses for H1 and H3 only. Wilson
intervals and exact McNemar discordant counts may be shown per replicate as
descriptive compatibility analyses, but they are not primary inference.

The confirmatory Holm family contains exactly three one-sided contrasts at
familywise alpha 0.05:

1. **H1:** `accuracy[RAW] - accuracy[G0] > 0`.
2. **H2:** `accuracy[C3] - 0.5 * (accuracy[RAW] + accuracy[G0]) >= 0`.
3. **H3:** `accuracy[C3G] - accuracy[C3] > 0`.

C1 and C2 comparisons are exploratory. Report H2's recovery fraction

```text
(accuracy[C3] - accuracy[G0]) / (accuracy[RAW] - accuracy[G0])
```

only if H1 survives Holm and its denominator is positive. Otherwise H2 is
non-evaluable. H2 is supported only when H1 survives Holm, the registered H2
contrast is nonnegative, its Holm-adjusted one-sided p-value is below 0.05, and
its one-sided Patient-cluster lower bound is at least zero.

## 6. Exhaustive graph decision regions

For episode `(q,r)`, let `D[q,r]` be the set of unique resource identities
disclosed to the answer model through either the graph packet or returned FHIR
content, and let `G[q]` be the gold-needed identities. Uniqueness is within an
episode only: the same resource disclosed in 20 episodes counts once in each of
those 20 episode denominators.

Define minimum-necessary micro precision and needed-set recall as:

```text
P_micro = sum[q,r] |D[q,r] intersect G[q]| / sum[q,r] |D[q,r]|
R_micro = sum[q,r] |D[q,r] intersect G[q]| / sum[q,r] |G[q]|
```

Every returned or packet-disclosed FHIR resource counts in the precision
denominator, including off-gold resource types. Resources read internally during
graph compilation but not disclosed to the answer model are excluded from
`P_micro` and reported separately as backend traversal exposure. Empty-needed-set
questions remain in the precision denominator when they disclose resources;
recall excludes empty-needed-set questions and reports its effective denominator.
If the full-cell precision denominator is zero, precision is undefined and C3G
cannot promote. Every cluster-bootstrap draw recomputes the ratio; report the
zero-denominator draw count and invalidate the precision bound if any occur.

- **Promote statistically:** C3G−C3 is at least +0.08, Holm-adjusted H3
  `p < 0.05`, its cluster interval lower bound exceeds zero, precision is at
  least 0.70, and the cluster-bootstrap precision lower bound is at least 0.70.
- **Does not earn its complexity:** as a preregistered product decision based on
  the point estimate—not proof that the true effect is below five points—the
  registered C3G−C3 point estimate is below +0.05 or precision is below 0.70.
- **Indeterminate; do not promote:** every other outcome, including the old
  5–8 point gray zone, an 8+ point estimate that does not clear corrected
  inference, or precision whose point estimate but not lower bound clears 0.70.

These are statistical/mechanism decisions, not authorization to deploy a
graph-backed product interface.

Keep the legacy benchmark precision for comparability only. Also report all
resources touched, returned bytes and fields, temporal breadth, graph build
and traversal reads, evidence disclosed to the answer model, compilation
latency, accepted-attempt and all-attempt model tokens, FHIR calls, wall time,
and a versioned hypothetical API-price conversion.

## 7. Judge gate

Use frozen deterministic normalizers only for semantics they can prove.
Booleans must be normalized to explicit yes/no semantics, not numeric substring
presence. Send ambiguous and free-text cases to an arm-blind judge from a model
family disjoint from the answer model.

Before test scoring, validate the exact judge prompt/model on 120 burned-dev
free-text pairs: 60 adjudicated-correct and 60 adjudicated-incorrect, stratified
across wrong-value, non-answer, insufficient-data, and formatting variants.
Two blinded humans label independently under a frozen rubric; adjudicate
disagreements and require Cohen's kappa at least 0.80.

Use three judge votes per item and majority vote both in calibration and test.
Require majority-vote TPR and TNR each at least 0.95, each Wilson 95% lower
bound at least 0.85, and self-flip rate at most 0.02. Self-flip rate is the
number of calibration items with a non-unanimous three-vote verdict divided by
120. If any gate fails, do not score the sealed test.

Before sealing, assign every gold target one frozen semantic class:
`ANSWERABLE_VALUE`, `ANSWERABLE_ABSENCE`, or—only if the benchmark explicitly
defines it—`UNANSWERABLE`. On `ANSWERABLE_VALUE`, epistemic abstentions such as
“cannot determine” and “insufficient data” score incorrect. On
`ANSWERABLE_ABSENCE`, only an affirmative absence claim matching the gold
semantics may score correct; uncertainty remains incorrect. An abstention can
score correct only for a pre-existing `UNANSWERABLE` class. Hash-bind the class
mapping, normalized phrase rules, and tests before test.

Exact normalized infrastructure/model sentinels—empty output,
`PLANNING_FAILED`, `CLI_ERROR`, `MODEL_ERROR`, and `TIMEOUT`—score incorrect.
These deterministic cases never reach the judge. Cache every verdict and vote
by answer hash.

## 8. Semantic-empty recovery and operational retries

Model-format behavior and transport failure are separate:

- Malformed planning receives at most two JSON reprompts. Continued malformed
  output follows the frozen forced-answer path and is scored; it is never
  retried as infrastructure.
- A semantic empty is a successful, parseable FHIR search Bundle with zero
  resource entries. A direct-read 404, timeout, 429, 5xx, malformed response,
  or empty model stream is not a semantic empty.
- C2, C3, and C3G receive exactly one recovery opportunity after the first
  semantic empty: one deterministic notice and at most one additional
  planner/fetch round. Repeated empties receive no further recovery. G0 and C1
  receive none.
- Each model or FHIR transport call permits at most three attempts for the
  frozen, machine-detectable retryable errors. An episode with exhausted
  transport may restart once. Accept the first operationally complete episode;
  never retry based on answer content, retrieval quality, or judge result.
- Preserve every failed attempt and include all-attempt tokens, latency, and
  calls in economics. Do not score a batch until every scheduled cell has one
  accepted completion or is declared an integrity failure under this rule.

## 9. Prospective power gate

The original fixed statement that `n=79` has an MDE near 0.10 is withdrawn.
Paired power depends on discordance, Patient clustering, and the registered
multiple-comparison threshold.

Before selecting or running the new holdout, estimate from dev-only C3G-vs-C3
runs and the proposed public holdout layout:

- `p_d,U`: for each replicate separately, the Patient-cluster-bootstrap 95%
  upper bound on paired binary discordance; use the maximum across replicates;
- `rho_p`: within-Patient ICC of the paired C3G−C3 contrast, truncated below at
  zero;
- `m_bar` and `CV_m`: mean and coefficient of variation of questions per
  Patient in the proposed holdout layout.

For target effect `delta = 0.08`, 80% power, and conservative first-step Holm
level `alpha = 0.05 / 3`, calculate:

```text
n_iid = ceil(((z[1-alpha] + z[0.80])^2
              * (max(p_d,U, delta) - delta^2)) / delta^2)

DE = 1 + (((1 + CV_m^2) * m_bar) - 1) * max(rho_p, 0)
n_required = ceil(n_iid * DE)
```

This equation is a conservative single-replicate screening calculation, not the
final power guarantee. Before sealing, run a fixed-seed Monte Carlo simulation
under a paired-Bernoulli Patient-cluster model with mean effect 0.08,
discordance `p_d,U`, contrast ICC `rho_p`, the exact proposed Patient cluster
sizes, three replicates, and the registered wild-cluster/Holm analysis. Freeze
the simulator source, RNG algorithm, seed, parameters, and receipt. Require
estimated power of at least 0.80.

The grouped, stratified holdout must satisfy both the analytic screen and the
simulation, using the larger required sample, and contain at least 40 Patient
clusters. Whole-Patient selection may overshoot the question target.

For scale: before cluster inflation, this conservative rule requires about 267
questions when discordance is 0.20, 405 at 0.30, and 681 at 0.50. If the
untouched eligible corpus cannot satisfy the power gate, do not consume it or
call the study confirmatory. Enlarge the corpus first, or label the run
estimation-only with no promotion, “proved,” or “killed” claim.

## 10. Atomic bundle and launch gates

Before any confirmatory answer call, one immutable manifest must bind:

- the new split and public-input hashes;
- gold hash and off-host custody receipt;
- FHIR snapshot and source-parity receipt;
- solver, graph compiler, prompts, scorer, analysis, and controller hashes;
- answer/judge model identities, effort, CLI/API versions, timeouts, output
  limits, and effective-model verification;
- arm-order schedule, replicate count, retry policy, pricing version, and
  judge-calibration receipt.

The solver environment must contain an explicit allowlist only: no repository
history, gold, previous results, inherited secrets, general host filesystem, or
unrestricted network. Any shell/command event, unexpected tool call, forbidden
path, model mismatch, input drift, partial arm, duplicate launch, or second
scoring attempt fails closed.

Required controller states are:

```text
SEALED -> RUNNING -> ANSWERS_COMPLETE -> SCORED -> CLOSED
```

The first confirmatory answer call burns the holdout. Scoring is one-shot and
aggregate-only. Until the new corpus, executable bundle, judge, power, and
isolation receipts all pass, **development may continue only on burned data and
synthetic fixtures; no confirmatory experiment is runnable.**
