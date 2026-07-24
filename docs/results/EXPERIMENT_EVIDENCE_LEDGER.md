# Canonical experiment evidence ledger

Updated: 2026-07-23

This is an aggregate-only claim ledger. It does not contain answer text, raw
clinical records, credentials, or hidden chain-of-thought. Numeric claims are
licensed only at the scope shown below.

## Experiments

| Experiment | Status | Population | Registered result | Decision |
|---|---|---:|---|---|
| A6a initial 409 | `confirmatory_promoted` | 409 questions / 90 patient clusters | Question-only selection 54.3% versus blunt projection 44.7%; +9.5pp, cluster 95% CI +5.4 to +13.7, McNemar p=1.3e-5. | License the paired selection claim; absolute accuracies are lower bounds because the shared time assumption was omitted from both prompts. |
| A6a run 2 | `confirmatory_promoted` | 409 questions / 90 patient clusters | Question-only selection 54.5% versus blunt projection 46.2%; +8.3pp, cluster 95% CI +3.8 to +12.9, McNemar p=0.000183. | The selection result survives the repaired time-assumption prompt and pinned model runtime. |
| QT-1 include pinning | `null_not_promoted` | 409 paired questions | Include pinning 54.0% versus A6a 53.8%; +0.24pp, cluster 95% CI -1.44 to +1.99, McNemar p=1.0. | Preserving referenced resources alone did not improve correctness. |
| QT-2 aggregate summary | `null_not_promoted` | 409 paired questions | Aggregate summary 54.5% versus A6a 54.8%; -0.24pp, cluster 95% CI -2.36 to +2.06, McNemar p=1.0. | Summaries reduced abstention but did not reduce errors, so the feature was not promoted. |
| QT-3 endpoint reserve | `null_not_promoted` | 409 paired questions | Endpoint reserve tied A6a at 54.8%; 0.0pp, cluster 95% CI -1.44 to +1.37, McNemar p=1.0. | Endpoint reservation alone was null and was not promoted. |
| Exploratory model/effort grid | `invalid_for_claims` | 99 questions per cell / six model-effort cells | Archived aggregates are internally inconsistent: every cell records 99 answered items but uses n=409 as the accuracy denominator. | Exclude the grid from every generality claim until it is deterministically regraded with the registered 99-question denominator. |
| QT-4 micro42 mechanism screen | `exploratory_advanced_to_confirmation` | 42 inspected microbiology questions | Vocabulary versus A6a +42.9pp (95% CI +20.5 to +63.6); traversal versus vocabulary +7.1pp (95% CI 0 to +15.9). | Advance vocabulary to untouched confirmation; do not promote traversal from the same-set screen. |
| QT-4 valid374 holdout | `confirmatory_promoted` | 374 questions: 44 dispatched / 330 byte-identical controls | Vocabulary versus A6a on dispatched questions +34.1pp (95% CI +17.9 to +50.0); traversal versus vocabulary +9.1pp (95% CI 0 to +20.9). | Promote fixed microbiology vocabulary only; traversal remains unresolved. |
| A11 V/T/E path-required | `confirmatory_supported_not_promotion_gated` | 120 constructed questions / 96 answerable path-required cases | T and E recovered terminal evidence on 96/96 answerable cases versus 0/96 for V. E versus T was +0.83pp with 95% CI 0 to +2.56. | Bounded traversal worked on the constructed multi-hop task. Do not promote event grouping: its single gain is not isolated from the bundled answerability receipt. |
| A11b causal isolation | `exploratory_not_promoted` | 64 development Patients + 384 untouched efficacy Patients at run start; one efficacy question per Patient; 1,152 answer slots | Strict normalized artifacts: 288/384 in every arm with paired-difference 97.5% intervals 0.0 to 0.0. Raw insufficiency behavior: 26/96 T0, 96/96 T1, 96/96 E1. Conservative post-hoc sensitivity: 314/384, 384/384, 384/384. | Preserve the strict artifact but supersede its behavioral null. T1 merits a fresh prospective test; E1 showed no benefit beyond T1. No promotion is licensed; the Patients are spent. |
| A11b successor zero-model build | `development_ready_not_answered` | 448 fresh synthetic Patients; 64 development packets materialized; 384 efficacy Patients reserved and unopened | Two clean roots produced byte-identical generation receipts and development public/audit trees under the v2 categorical answer contract. | Seal and independently approve the 192-answer development probe. Do not open efficacy unless both registered correctness contrasts have nonzero discordance. |
| W1A deterministic prejoin | `exploratory_supported_grading_sensitivity_pending` | 409 questions / 90 patient clusters; 176 visit-specific | Pooled +2.0pp (95% CI -0.9 to +4.5, p=.256); visit-specific +6.8pp (95% CI +1.8 to +12.4, p=.0075). | Support the deterministic join mechanism provisionally. Do not license the effect size until an opaque sensitivity grade closes historical arm-label exposure. |
| W2A agent-side join | `exploratory_unresolved_grading_sensitivity_pending` | 176 visit-specific questions | +4.0pp versus prejoin; 95% CI -8.7 to +17.6, p=.41; 30 agent-only versus 23 prejoin-only wins. | No difference detected and no equivalence established. Develop any hybrid only on burned data, then use a fresh patient-disjoint confirmation. |

## Claim register

### Deterministic question-aware selection beats query-blind projection on this FHIR benchmark.

Disposition: **licensed**.

Supported twice on the 409-question paired corpus, including the repaired time-assumption run. It is not yet a cross-model, cross-server, or natural-chart claim.

Evidence: `a6a-initial409`, `a6a-run2-assumption-fixed`.

### A fixed microbiology vocabulary improves the selected evidence packet.

Disposition: **licensed**.

Confirmed on the untouched valid-split dispatched stratum with byte-identical negative controls. The claim is terminology- and benchmark-specific.

Evidence: `qt4-micro42-v3b`, `qt4-valid374-v2`.

### Bounded traversal can recover terminal evidence required by constructed multi-hop FHIR questions.

Disposition: **bounded_support**.

A11 establishes the mechanism on its synthetic path-required benchmark. It does not establish a native graph database, universal graph superiority, or natural-chart generality.

Evidence: `qt4-valid374-v2`, `a11-vte-120`.

### Typed event grouping improves accuracy beyond identical evidence and deterministic aids.

Disposition: **pending**.

A11 bundled grouping with temporal rank and an answerability receipt. A11b's normalized labels tied, but its forensic amendment found that normalization erased T1/E1's raw insufficiency behavior. E1 still showed no incremental benefit beyond T1. The run cannot license a confirmatory claim, and a fresh discriminating holdout is required.

Evidence: `a11-vte-120`, `a11b-causal-isolation-384`.

### Resolving a visit-to-resource join before answer generation improves visit-specific accuracy on this benchmark.

Disposition: **exploratory support; sensitivity pending**.

W1A's planned visit-specific subset improved by 6.8 points. Most labels came
from a historical panel whose model-visible IDs exposed arm names, so the
effect is not yet licensed as grading-robust or general.

Evidence: `w1a-prejoin-409`.

### Agent-side joining matches or beats deterministic prejoin accuracy.

Disposition: **not established**.

W2A's point estimate was +4.0 points, but p=.41 and the patient-cluster interval
spans -8.7 to +17.6. The run did not test equivalence and used 4.061 times the
cumulative input tokens on the same questions.

Evidence: `w2a-agent-join-176`.

### The effect generalizes across model sizes and reasoning levels.

Disposition: **not_established**.

The exploratory grid aggregate used the wrong denominator and is excluded. Cross-API/model generality remains untested.

Evidence: `generality-grid-99`.

### Bonfire should replace its canonical store with a native graph database.

Disposition: **not_established**.

No experiment compared storage engines. Current evidence concerns packet selection, terminology, traversal, and presentation only.

Evidence: `qt4-valid374-v2`, `a11-vte-120`.

## Economics receipt coverage

| Experiment | Accepted tokens | All-attempt tokens | Notes |
|---|---:|---:|---|
| A6a initial 409 | not retained | not retained | Packet payload was 36.7M versus 64.0M characters; historical token receipts were not consolidated in the committed result. |
| A6a run 2 | 66818190 | 67635039 | Across both arms. Selection used 26,207,434 accepted tokens versus 40,610,756 for blunt projection; all-attempt totals were 26,668,834 versus 40,966,205. |
| QT-1 include pinning | 25718285 | 25718285 | A6a comparator accepted 26,207,434 tokens in run 2. |
| QT-2 aggregate summary | 26213539 | 26213539 | A6a comparator accepted 26,207,434 tokens in run 2. |
| QT-3 endpoint reserve | 25641382 | 25641382 | A6a comparator accepted 26,207,434 tokens in run 2; one non-JSON warning line was ignored when summing complete usage receipts. |
| Exploratory model/effort grid | not retained | not retained | Not consolidated because the analysis denominator is invalid. |
| QT-4 micro42 mechanism screen | 5672506 | 5672506 | Three answer arms; zero failed attempts. Panel usage is not included in this subtotal. |
| QT-4 valid374 holdout | 60760432 | 63737565 | Panel used 1,820,656 tokens; aborted v1 protocol overhead was 3,546,961 tokens and is reported separately. |
| A11 V/T/E path-required | 4728676 | 4728676 | Answer arms only; panel added 445,171 tokens. Zero retries. |
| A11b causal isolation | 24481563 | 24568225 | Unregistered answer preview only; all-attempt usage is a lower bound because three pre-inference rejects lacked usable usage. Panel added 1,987,299 tokens. |
| A11b successor zero-model build | 0 | 0 | Source generation, packet compilation, and receipt verification only; no answer or judge call. |
| W1A deterministic prejoin | 22244468 | 22244468 | 409 complete answer usage receipts; derived input plus output. Comparator used 28,920,106 tokens. Provider-priced cost was not retained. |
| W2A agent-side join | 33893257 | 33893257 | 176 complete answer usage receipts; derived input plus output. Agent join used 4.061 times the cumulative input tokens of prejoin on the same questions. |

## Known evidence gaps

- The exploratory model/effort grid must be regraded on its registered 99-question subset before any generality statement.
- A6a and QT-1 through QT-3 retained subscription token receipts but not provider-priced monetary cost.
- The A11 panel used the same model family as the answerer; a cross-family sensitivity judge remains useful.
- The A11b preview completion replay validated prompt, answer, schema, event, usage, and artifact receipts. Its exposure booleans were self-asserted rather than independent measurements.
- A11b r3 is not runnable as sealed against the current Codex backend. Its successor now has a fresh reproducible development corpus and categorical answer contract, but still requires a new development controller seal and prospective nonzero discordance.
- The 384 A11b r3 efficacy Patients are spent. The successor's separately reserved 384 efficacy Patients remain unopened and cannot be materialized before the development gate passes.
- No native graph database, Postgres recursive-query, or natural clinical chart comparison has been run.
- W1A and W2A historical panel item IDs exposed arm names. A fresh opaque
  sensitivity grade is required, but no healthcare-derived queue may be sent
  to an external judge without an approved data route.
- W1A and W2A answer manifests did not retain an explicit answer-model or
  reasoning-effort pin. Their local protocol files were not independently
  Git-anchored before the runs.
