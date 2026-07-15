# A11 post-result forensic audit

Status: **complete - no hidden-gold or tool-use contamination found; primary
gain is not attributable to event grouping alone**

Completed: 2026-07-15, after the registered final result was sealed

## Scope

This audit opened answer content only after all 360 answers, deterministic
grading, the pinned arm-blind panel, panel replay, and finalization were
complete. It used only the official A11 v1 controller namespace bound to
manifest SHA-256
`3f1209ebc750c7f9eeb67d0a7e5ed3a455aa91dbda2be2ffd4c1905fe192fdce`.

The audit asked four questions:

1. Did any arm receive hidden gold, benchmark labels, or an arm identity?
2. Did any answer use tools, files, or an unsealed execution path?
3. Why did E beat T on exactly one question?
4. Do the answer traces support an event-grouping claim, or only a traversal
   and answerability claim?

## Verdict

There is no evidence that the run cheated through hidden gold, tool use,
cross-arm contamination, retries, or post-hoc input changes.

There is an important attribution limit. E deliberately included a
question-derived `answerability_receipt` whose `sufficient`/`insufficient`
state agreed with constructed benchmark answerability on 120/120 questions.
That is a preregistered deterministic feature, not leaked gold, but it is a
strong derived signal. The only E-over-T correctness gain occurred on an
unanswerable question where that receipt said `insufficient`.

Therefore A11 does not isolate typed event grouping as the cause of the gain.
The safe conclusion is that the E bundle prevented one temporal-binding and
answerability error; the receipt is the most direct proximal signal, while
event grouping and temporal rank may also have helped.

## Integrity checks

### Prompt leakage

All 360 exact prompts were scanned for the sealed dataset's gold-only fields,
including `reference_answer`, `nonselected_reference_answer`, `failure_mode`,
`answerable`, `terminal_resource_ref`, `selected_root_ref`, patient-policy
hashes, and explicit gold/correct-answer labels. Every count was zero in V, T,
and E.

The prompts included only the question ID, question text, the synthetic/non-PHI
assumption, and the arm payload. Question IDs were opaque hashes and did not
encode outcomes.

E did include its registered question plan, temporal selection, path
satisfaction, and answerability receipt. Those are the treatment, not hidden
benchmark columns. Their unusually close alignment with the constructed label
is disclosed rather than treated as independent evidence for event grouping.

### Execution contamination

- All 360 accepted answer receipts were complete.
- Accepted and all-attempt token totals matched exactly.
- There were zero retries, failed attempts, or error events.
- The per-answer event logs contained only thread start, turn start, one agent
  message, and turn completion/usage. No tool or filesystem events occurred.
- The answer runner used a read-only sandbox, isolated packet working
  directory, ignored user instructions/configuration, and the controller-pinned
  Codex `0.144.1` binary and `gpt-5.6-sol` model.
- T and E retrieval was identical on all 120 questions at mapping SHA-256
  `07b655b184b916d3bafe9c28f12ff92b520d800168f75a11ead5dfdf23574eca`.
- The arm-blind panel was replayed from its sealed queue and used no tools.
- All 192 panel items received unanimous 3-0 majorities.
- After verification, the three raw arm trees were made read-only and archived
  at SHA-256
  `1d609dbf96ce28dab2ca59cd9de12e2a79a8cd4fb3caf20c01df6dbb8e477449`.

## The single T failure

Question `a11q-7491d630596a71ed2e52` asked for the specimen used in the **first**
culture Observation through two intermediate Observations. It was a registered
unanswerable `observation_specimen:depth-3` case.

The flat T packet contained two candidate roots:

- the true first root at `2099-01-15T08:00:00-05:00`, whose terminal specimen
  edge was unavailable; and
- a later root at `2100-01-15T13:00:00Z`, whose complete path resolved to
  `Synthetic sample 91132A607B`.

T answered with the later specimen. Its own evidence summary explicitly called
the 2100 event "first" because the earlier event had no resolvable specimen.
The model silently changed the question from "first event" to "first event
with a complete answer." This was not a graph-construction failure: the packet
correctly represented both paths and marked the earlier terminal edge
unavailable. It was a temporal-binding and answerability error over a flat
representation.

E preserved the same retrieved evidence but grouped it by root event, marked
the 2099 group as `selected_for_question`, marked its required specimen path
unsatisfied, and emitted an `insufficient` answerability receipt. E correctly
abstained and explicitly rejected the later specimen as nonresponsive.

No hidden reasoning trace exists beyond the structured answer and evidence
summary; the Codex event stream records only the final structured message. The
explanation above is therefore based on the model's own written evidence
summary and the sealed packets, not an unrecorded chain of thought.

## Cross-arm answer review

- T and E made the same abstain-versus-answer decision on 119/120 questions.
- The only difference was the T failure above: T answered and E abstained.
- On all 96 answerable questions, T and E were both correct and cited the same
  source-resource set.
- Across one inspected success from each of the eight family/depth cells, T
  and E selected the same clinical fact and sources. Differences were wording
  and whether the code was repeated in the answer.
- T and E cited the same source set on 119/120 questions overall.

This is consistent with the aggregate result: traversal supplied the missing
terminal evidence, while E changed presentation and answerability behavior but
had almost no remaining accuracy headroom.

## V citation anomalies

V abstained on all 120 questions because its star packets never contained the
terminal evidence for the 96 answerable path-required cases. Two of those
abstentions cited an invalid source ID:

- `a11q-8c6343037157815d64cc` cited
  `Observation/7e763c36f0907047624d1f` instead of the present
  `Observation/7e763c36f0907047624d1f7f`.
- `a11q-e59d15c7e93ca83ea8ea` cited
  `Observation/af1a1980ed0c4ee5a6b1ad0` instead of the present
  `Observation/af1a1980ed0c4ee5a6b1ad0c`.

Both are one- or two-character truncation/copy errors in otherwise accurate
descriptions of the selected root. Both questions were answerable, so V was
already incorrect for abstaining. The citation anomalies did not create or
remove a correctness flip and do not explain either registered contrast.

## Mechanism conclusion

What worked:

- Bounded traversal placed the terminal fact in the packet on 96/96 answerable
  cases, versus 0/96 for V.
- The model used that evidence correctly on all 96 answerable T and E cases.
- E made temporal selection, path completeness, and insufficiency explicit and
  avoided the only T error.

What did not earn a claim:

- Event grouping's incremental accuracy benefit. E minus T was one question,
  and the registered interval includes zero.
- Independent causal attribution to grouping. E bundled grouping, temporal
  rank, typed edges, and the answerability receipt.
- Native graph storage. No database engine was part of the treatment.

The clean follow-up is a harder untouched holdout with at least three arms:
flat traversal, flat traversal plus the same deterministic answerability
receipt, and event groups plus that receipt. The first contrast measures the
receipt; the second isolates structure.

The panel was arm-blind but not model-family independent: it used the same
`gpt-5.6-sol` family as the answerer. Seventeen of 30 panel batches contained
multiple responses from the same synthetic record, which can induce correlated
judgments. No arm or host mapping appeared in those prompts, and the sealed
replay passed, but a cross-family judge remains a useful sensitivity check.
