# A6a artifact review — judge audit + failure forensics

*2026-07-12, day after the confirmatory run. Method: 10-agent review (Claude
family — deliberately cross-family vs the codex panel): two blind regraders
over a stratified 50-item panel sample (all 5 split votes + 45 random
unanimous), a blind 40-item audit of the deterministic grader, three
failure-forensics agents over precomputed bundles, and per-disagreement
adjudication. This is the sample-scale version of ROADMAP item 15.*

## 1. Judge quality: good, with three adjudicated errors — result unaffected

- **Cross-family agreement 47/50 (94%).** The codex panel is also extremely
  self-consistent (543/548 unanimous 3-0).
- Three disagreements, all adjudicated with justifications:
  1. Panel too lenient: a self-contradictory answer (leads "No", body
     concludes "the second was lower" = Yes) credited as correct.
  2. Panel too lenient: bare "observation" credited against gold
     "EU OBSERVATION" (MIMIC has 4 distinct observation admission types).
  3. Panel too harsh **and internally inconsistent**: an abstention on a
     null gold graded wrong in one arm while the same panel unanimously
     graded the other arm's materially identical abstention correct.
- **Deterministic-grader audit found a real defect class:** verbalized signed
  differences. "MCH **decreased by 0.1** pg" is −0.1 but number-containment
  finds no −0.1 token → false negative (3 instances, both arms). Plus a
  midnight-placeholder failure shared with the panel: gold
  `2142-05-10 00:00:00` is a date-only `chartdate` placeholder; an answer
  giving the same date **with the real time** was graded wrong (both arms).
- **Robustness reanalysis** with all identified corrections applied (8 label
  flips, both directions, both arms):
  **A6a 54.8% vs A0′ 45.2% — diff +9.5pp exactly unchanged, discordant
  59v20 unchanged, p=1.3×10⁻⁵ unchanged, CI [+5.2, +13.8].**
  The errors were symmetric; the published conclusion does not move.

## 2. CONFIRMED harness defect: the time assumption never reached the model

The benchmark rows carry `assumption` (e.g. *"Assume the current time is
2178-12-31 23:59:00. Search for 'Daily Weight' to find weight."*). The packet
builder uses it for date windows — but `codex_harness.build_prompt` **never
includes it in the answering prompt**. Verified: 0 occurrences of "current
time" across all 818 prompts.

Consequences: on relative-date questions ("since 14 months ago", "this
year") the model has no "now" — MIMIC's future-shifted dates make guessing
impossible — and its abstentions were *correct given its information*
(~7–8 per arm graded wrong). The retrieval hints some assumptions carry were
also withheld. **Both arms were affected identically, so the paired contrast
is unbiased; the absolute levels (54.3%/44.7%) are lower bounds.** Fix is
one line; per prereg §8 any post-freeze harness change makes the rerun a
separately-labeled run — proposed as **run 2 (pre-declared, prompt-fix
only)** to measure the absolute-level correction, with run 1 remaining the
registered primary for the contrast.

## 3. Failure forensics — where each arm actually loses

**Microbiology (worst stratum, 14%/10%) is ~85–90% a retrieval-expressiveness
failure, 0% grading, ~1 case reasoning.** The gold micro Observation is
category `laboratory` (no `microbiology` category exists), distinguished only
by a MIMIC-specific CodeSystem with displays like "URINE CULTURE"/"MRSA
SCREEN"; the organism hangs off a child Observation via `hasMember`. The
planner's NL terms ("microbiology test") match nothing → all 42 queries
relaxed to bare date-sorted fetches → the ~5–15 micro observations drown
under a median 1,487 routine labs and are evicted by bounds (median 176
kept). Gold was in-packet in 2/30 (A6a) and 0/30 (A0′) value questions.
**qo-v4 target #1: a micro-aware sub-planner** (culture/screen display
vocabulary + `hasMember`/`Specimen` traversal).

**The `gold_in_packet` flag overstates "recoverable" failures.** Of 26
abstained-with-evidence cases, ~19 are not recognition failures:
7 aggregation questions whose gold COUNT/SUM exceeds what the cap retained
(you cannot count 56 distinct medications from 50 retained rows — abstention
was correct), 7–8 temporal-anchor cases (§2), 3 first-vs-last questions
where the recency cap dropped the "first" endpoint, 3 missing
`_include` Medication displays (the model correctly refuses to name a drug
whose name isn't packed). The evidence-present-wrong class (51 rows) splits:
41% wrong extraction/computation from the right cited resource (incl.
`authoredOn`-vs-`starttime` near-misses and attention collapse when summing
many small resources), 24% wrong-evidence pick, 16% benchmark data-model
ambiguity, 10% the grading errors above, 10% temporal anchor.

**Hallucination-risk class (38 answered-without-gold) is mostly systematic,
not confabulation:** the dominant patterns are type-confusion (answering
from an adjacent lab when the asked-for observation is missing) and the same
temporal-anchor bug; genuinely dangerous confident fabrications are a small
minority — but nonzero, and they motivate keeping the citation-support
check in every future arm.

## 4. Action list (ranked)

1. **Harness: inject `assumption` into the prompt** (one line) + pre-declare
   run 2. Affects every future arm.
2. **Grader: verbalized-sign equivalence + date-only placeholder handling**
   (deterministic rules; fixes a repeatable FN class in both graders).
3. **qo-v4: micro sub-planner** (vocabulary + hasMember/Specimen) — worth
   up to ~35 questions where both arms currently score ~0.
4. **Bounds: aggregation-aware mode** — when the question asks COUNT/SUM,
   per-type recency caps are the wrong selection policy; consider count
   summaries or full-ID lists (this is the A10-AGG deterministic-reducer
   direction).
5. **Packet: guarantee `_include` targets survive bounding** (Medication
   displays riding MedicationRequest).

Raw agent reports and adjudications: workflow `wf_8c06c2bb-c40` journal;
bundles under the session scratchpad `a6a-review/`.
