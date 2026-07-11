# Pre-registration: A6a — question-only packet selection vs blunt projection

**Status:** FROZEN pending confirmatory run · v1.0 · 2026-07-11
**Planner/harness freeze commit:** `14e839a` (merge of PR #13; planner `qo-v1`)
**Registered before:** any A6a run on the confirmatory set. Amendments after this
commit are documented in a "Deviations" section and demote affected analyses to
exploratory.

This is the **program's single primary confirmatory hypothesis** under the
family-level statistical policy in [ROADMAP.md](../ROADMAP.md). It is designed
against findings 1–7, 11, and 30 of the
[2026-07-11 adversarial review](../reviews/2026-07-11-adversarial-roadmap-review.md);
the mapping is at the end.

---

## 1. Question

Does deterministic **question-only** query-aware selection (plan the FHIR fetch
from the question text, patient ID, and assumption alone — nothing the
benchmark constructed) beat **query-blind blunt projection** at equal packet
discipline, on the same substrate, same instance, same grading?

This isolates exactly one treatment: *what gets selected into the packet*.
Renderer, bounds, substrate, prompts, schema, and grading are held identical.

## 2. Hypotheses and decision rule

- **H1 (primary, confirmatory):** A6a pooled accuracy > A0′ pooled accuracy on
  the 409-question test split, paired per question.
- **H0:** no difference.

Decision rule, committed in advance:

| Outcome | Licensed claim |
|---|---|
| A6a > A0′ (p<.05, cluster-CI excludes 0) | "Deterministic question-only selection beats query-blind projection" — the selection lever survives removal of all oracle metadata. |
| No detectable difference | "The qo-v1 deterministic floor does not beat blunt projection" — published as-is; the lever claim then rests only on the (separately labeled) oracle ceiling, and the next step is a better *question-only* planner, not a better story. |
| A6a < A0′ | Same as above, published as-is, plus failure-taxonomy analysis. |

No outcome licenses: "matches the sandbox" (requires contemporaneous A5 —
secondary, gated, §7), any serialization claim (A6b), any coverage claim (A6c),
or any product claim.

## 3. Arms

Both arms: same Medplum instance (local docker-compose, MIMIC-IV-on-FHIR demo,
load recorded by scripts + UUID manifest), same substrate (Codex CLI,
subscription-billed, pinned version recorded in every run manifest), same
answer schema (`schemas/codex_answer.schema.json`), same prompt template
modulo the packet payload, temperature/effort at harness defaults, one attempt
per question.

- **A0′ (control):** blunt query-blind projection — fetch the patient record,
  strip `text`/`meta`/`extension`/`modifierExtension`, recency-cap 50 per
  resource type. Rebuilt **contemporaneously on this instance** (the historical
  A0′ numbers are not reused).
- **A6a (treatment):** `a6_packet_builder.py --planner question-only`
  (`qo-v1`), bounds `--max-total-resources 120 --max-packet-chars 100000`,
  single renderer (projected raw JSON), **no** coverage summary, **no**
  serialization variants, frozen packets with SHA-256 manifests.

**Reported beside, never as headline:** A6-oracle (`--planner
metadata-oracle`) — the ceiling using benchmark-construction metadata. Labeled
"oracle ceiling" in every table it appears in.

## 4. Data discipline

- **Dev slice:** the `valid` split of `questions_answers_sql_fhir.csv`
  (424 questions). Used for mechanics and planner tuning. **Disclosure of dev
  usage to date:** 12 valid-split plans inspected and 2 answered live during
  harness bring-up (2026-07-11); three qo-v1 fixes came from that inspection
  ("of"-connector terms, `in M/this year` windows, undergo→Procedure). Up to
  ~50 more dev questions may be used to tune qo-v1 **before** freeze-for-run;
  every dev run is committed under `runs/` with manifests.
- **Confirmatory set:** `full_test409.csv` (409 questions, ~90 patients).
  Untouched by planner tuning. The planner is frozen (tagged commit) before
  the first confirmatory packet is built. One confirmatory run; no retries of
  the run after seeing results.

## 5. Grading

The existing trustworthy pipeline, unchanged: deterministic numeric checks
where unambiguous; otherwise the multi-vote panel with cross-family check
(`build_labels.py` → `final_grade.py`). Judges never see arm identity.
Boolean golds (`[[1]]`/`[[0]]`) route to the panel per the documented fix. The
57 unanswerable-by-design questions follow the pipeline's existing abstention
semantics. Known open issue: judge re-measurement (ROADMAP item 16) is
pending; per the ordering gate, the published headline ships after item 16
resolves or carries an explicit caveat naming it.

## 6. Statistics

- **Primary test:** exact paired McNemar on correct/incorrect, A6a vs A0′,
  two-sided α = .05.
- **Uncertainty:** patient-cluster bootstrap CI on the accuracy difference
  (~90 patient clusters), reported alongside.
- **Multiplicity:** H1 is the program's only confirmatory hypothesis — no
  correction needed for it; every other comparison in this document is labeled
  exploratory and reported with CIs, no stars.
- **Power note (honest):** with 409 pairs, detectable effects depend on the
  discordant-pair rate; at 20–30% discordance the MDE is roughly 6–9pp at 80%
  power. Smaller true effects may be missed; a null is reported as "not
  detected at this power," not "absent."

## 7. Pre-treatment strata and secondary metrics (all exploratory)

Strata defined **before and independent of any arm's behavior** (finding 7):

1. **Record size tertiles** — independently serialized character count of the
   patient's full FHIR record, computed directly from the store before any arm
   runs.
2. **Answer type** — numeric / boolean / other, from the gold answer format.
3. **Source domain** — `main_table_name` (used for *stratified reporting
   only*; the A6a planner never sees it).

Legacy A0-overflow strata appear only in a descriptive appendix, labeled
post-hoc.

Secondary metrics per arm: citation support rate (cited resource IDs exist in
the packet), abstention rate and abstention-appropriateness, packet
chars/tokens, cost and calls, and for A6a a planner-failure taxonomy on wrong
answers: type-miss / term-miss / window-miss / bounds-drop / present-but-wrong
(the last is the reasoning residual).

**Secondary contrast (gated):** A6a vs a contemporaneous same-instance,
same-substrate sandbox arm (A5-equivalent), only if/when that arm is rerun
under these controls. Until then, no sandbox comparison is made or implied.

## 8. Exclusions and edge cases

- No question exclusions. Empty packets are answered (expected: abstention)
  and scored by the pipeline like any other answer.
- Substrate/API failures: one retry for transport-level failure only
  (recorded); persistent failure scores as unanswered and is reported.
- Any harness change after freeze = amendment + the affected run is labeled
  exploratory.

## 9. Review-findings mapping

| Finding | How this design answers it |
|---|---|
| 1 (oracle metadata) | Whitelist planner; oracle arm quarantined as labeled ceiling |
| 2 (unbounded) | Hard resource/char ceilings + projection + per-query fetch cap, stats in manifests |
| 3 (bundle) | A6a manipulates selection only; serialization (A6b), coverage (A6c), dedup ablation (A6d) are separate future arms |
| 4 (serialization confound) | Single fixed renderer in both arms |
| 5 (coverage leakage) | No coverage summary in A6a |
| 6 (cross-substrate pairing) | Both arms contemporaneous on one instance, one substrate; historical numbers never paired against |
| 7 (post-hoc strata) | Pre-treatment strata (§7); legacy strata descriptive only |
| 11 (winner's curse) | Dev/confirmatory split with disclosed dev usage; frozen planner; one confirmatory run; declared primary contrast |
| 30 (family policy) | This is the single confirmatory hypothesis; all else exploratory |

## 10. Deviations

None yet. (Amendments append here with date, reason, and the demotion of any
affected analysis to exploratory.)
