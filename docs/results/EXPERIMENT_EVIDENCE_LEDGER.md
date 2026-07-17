# Canonical experiment evidence ledger

Updated: 2026-07-16

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
| A11b causal isolation | `exploratory_not_promoted` | 64 development Patients + 384 untouched efficacy Patients; one efficacy question per Patient; 1,152 answer slots | Unregistered r3 preview: T0, T1, and E1 each scored 288/384 (75.0%). Both paired contrasts were 0.0pp with cluster 95% CI 0.0 to 0.0. All arms were 288/288 on answerable cases and 0/96 on unanswerable cases because every arm answered instead of abstaining. | Exploratory do-not-promote. The corpus ceilinged on supported cases and exposed a shared abstention failure on unsupported cases. No registered claim is licensed; the used efficacy Patients are spent, and any confirmatory/API run needs a fresh holdout plus a newly sealed backend-compatible schema. |

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

A11 bundled grouping with temporal rank and an answerability receipt. The unregistered A11b r3 preview found an exact T0/T1/E1 tie, but the run cannot license a confirmatory claim and the corpus had no answerable-case headroom. A fresh discriminating holdout is required before this claim can be resolved.

Evidence: `a11-vte-120`, `a11b-causal-isolation-384`.

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

## Known evidence gaps

- The exploratory model/effort grid must be regraded on its registered 99-question subset before any generality statement.
- A6a and QT-1 through QT-3 retained subscription token receipts but not provider-priced monetary cost.
- The A11 panel used the same model family as the answerer; a cross-family sensitivity judge remains useful.
- The A11b preview completion replay validated prompt, answer, schema, event, usage, and artifact receipts, but a separately implemented independent raw-audit adapter and cross-family panel sensitivity remain undone.
- A11b r3 is not runnable as sealed against the current Codex backend: oneOf and uniqueItems are rejected in response-format schemas; an official run requires a new seal.
- The 384 A11b efficacy Patients are spent for confirmatory use; a replacement must demonstrate nonzero paired discordance on separate development data before a fresh holdout or cross-API matrix is opened.
- No native graph database, Postgres recursive-query, or natural clinical chart comparison has been run.
