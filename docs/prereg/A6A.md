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
  (`qo-v2`; see Amendment 1), bounds `--max-total-resources 200
  --max-packet-chars 160000` (amended, see Amendment 1),
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

**Amendment 1 (2026-07-12, pre-freeze — before any confirmatory packet was built).**
Dev-slice tuning results, disclosed in full:

1. **Planner locked at qo-v2.** Three qo-v3 mechanisms were built and measured on
   the same 50 dev questions (gold-evidence-in-packet recall, n=32 gradeable):
   code-bucket bounding 20/32, term-priority buckets 21/32, bare-companion
   queries alone 21/32 — every variant ≤ qo-v2's 22/32. All three reverted;
   the planner ships as qo-v2 exactly as merged in PR #15. (Negative results
   recorded here per the winner's-curse discipline; the dev slice was reused
   across variants, which is why none of these comparisons is evidence of
   anything beyond "did not improve recall on dev.")
2. **Bounds amended: 120/100k → 200 resources / 160k chars.** A config sweep
   (not a new mechanism) measured recall 22/32 (69%) at 120/100k vs 24/32
   (75%) at 200/160k, with median packet 127k chars — still below the A0′
   control's 157k median. §3's A6a bounds are amended accordingly. The
   trade-off is acknowledged: the token-economics contrast vs A0′ narrows
   (~44% → ~19% median reduction) in exchange for a higher recall ceiling on
   the primary accuracy hypothesis.
3. **Root-cause note for the record:** the two "unsearchable" dev misses were
   a pagination-depth artifact (client caps at 10 pages; sorted-prefix depth
   depends on `_count`), not data absence. Deeper prefixes via larger
   `_count` were part of the reverted variants and are NOT in the frozen
   config; the misses stand as known planner-floor limitations.

Confirmatory discipline unchanged: one run, untouched 409, frozen planner and
bounds as of the freeze commit recorded in the run manifests.

**Amendment 2 (2026-07-12, post-run-1, PRE-DECLARED before run 2 starts).**
The artifact review (docs/A6A_ARTIFACT_REVIEW.md) confirmed a harness defect:
the benchmark's per-question `assumption` (reference "now" + retrieval hints)
never reached the answering prompt (0/818 prompts). Both arms were affected
identically, so run 1's paired contrast is unbiased and remains the
registered primary result. **Run 2** is hereby pre-declared: identical
packets (same SHA-256 manifests), identical arms and schema, ONE change —
the prompt now includes the assumption block. Grading: deterministic rules
det-v2 (verbalized-sign equivalence added; disclosed) + the same 3-vote
panel with two clarified rules (date-only placeholder timestamps,
verbalized signs). Purpose: measure the absolute-level correction; the
run-2 contrast is reported alongside run 1 regardless of direction or
significance. Prediction (falsifiable, non-binding): both arms rise, the
contrast persists. Run 2 outputs live under `runs/codex-*-test409-run2/`.

**Amendment 3 (2026-07-12, substrate-uniformity repair + model pinning).**
Post-hoc audit found the harness inherited each machine's codex config
default, so run 2 mixed models mid-run: ~80 questions/arm on gpt-5.6-sol@high
(laptop), the remainder on gpt-5.5@xhigh (mini). Within-pair contrasts stayed
model-consistent (the driver interleaves arms), but pooled accuracies and the
run-1-vs-run-2 delta were confounded. Repair, decided after an interim
deterministic-subset look at the mixed run (disclosed; per-question outcomes
not consulted): (a) every future invocation pins `--model gpt-5.6-sol
--reasoning-effort high` (recorded in run manifests); (b) the mini-answered
gpt-5.5@xhigh portions of run 2 and all of QT-1 are re-run under the pin;
(c) the gpt-5.5@xhigh answer sets are ARCHIVED intact and labeled an
exploratory second-model replication — reported, never deleted; (d) panel
grading moves to the mini account with the judge pinned to gpt-5.6-sol@high
(same judge model as runs 1's panel; account change disclosed). A
model×effort generality grid (gpt-5.6 luna/terra/sol × medium/high on a
seeded 100-question paired subset) is planned as a separate exploratory
stage.

**Interim-look disclosure**Interim-look disclosure (2026-07-11 ~20:45, run 86% complete).** At the
investigator's request, a deterministic-subset, answered-only interim look was
computed (111 paired numeric/unanswerable golds; all boolean/categorical golds
still panel-pending): A6a 56.8% vs A0' 40.5%, discordants 19 vs 1. No design,
planner, bounds, or run change followed; the run continued to completion
unchanged. Disclosed here because interim looks can bias reporting; the
primary result remains the full-run, full-grading table.
