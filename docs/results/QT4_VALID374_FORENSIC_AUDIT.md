# QT-4 valid374 forensic integrity and mechanism audit

Status: complete

Audit date: 2026-07-14

Scope: aggregate receipts plus post-freeze answer/event inspection after all
three arms and grading completed

## Verdict

No evidence of label leakage, hidden tool use, cross-arm contamination, or
gold-answer access was found. The measured vocabulary effect is consistent
with a real packet-construction change. Traversal recovered linked evidence,
but flat presentation and temporal binding limited its end-to-end value.

The audit also found protocol weaknesses worth fixing before the next run.
They do not invalidate this result, but they limit the strength of future
tamper-resistance and panel-process claims if left unchanged.

## No-cheating checks

- Reconstructed all **1,122** accepted prompts from sealed inputs and manifests.
- Found zero visible answer labels, gold FHIR IDs, SQL/procedure labels, query
  plans, feature flags, or arm names in model-visible prompts.
- Every answer call used an ephemeral thread, an empty temporary working
  directory, read-only instructions, and standard input.
- Event streams contained zero shell, filesystem, web, MCP, or other tool calls.
- The 330 negative-control prompts and packets were byte-identical across arms.
- The arm-blind panel received opaque batches without arm or question IDs.
- Every disagreement routed to the panel was unanimous across its three votes.

One citation appeared outside the packet's top-level resource list, but it was
exactly visible inside a serialized reference field and was not a mapped gold
resource. That is citation-scope overreach, not hidden retrieval. Overall,
3,254 of 3,255 citations named top-level resources.

## Retry and transport review

There were 57 discarded attempts: 20 A6a-r, 20 QT-4V, and 17 QT-4T. Each had a
complete tool-free structured answer and was rejected because model-list
refresh warnings appeared on stderr. The sealed runner treated those warnings
as transport-invalid and retried without looking at correctness.

This is correctness-blind resampling under the frozen runner and is fully
included in all-attempt economics. It is not evidence of cherry-picking. The
next preregistration should classify benign provider warnings explicitly so a
complete valid answer is not discarded for unrelated stderr noise.

The accepted streams showed one provider reconnect timeout that recovered
within the same attempt and did not create a retry or receipt mismatch.

## Mechanism audit

### Vocabulary versus A6a-r

The 44 dispatched packets became much smaller and more targeted: average root
resources fell from roughly 179 to 18, average packet size from about 169 KB to
18 KB, and targeted input tokens from 3.89M to 0.80M in aggregate.

The baseline planner extracted a narrow `code:text` term and, when that missed,
relaxed to a broad Observation fallback. The frozen vocabulary arm instead
used a fixed microbiology union covering culture, Gram stain, screening, and
smear language without the generic fallback.

Of the 17 discordant answers, 16 favored vocabulary and one favored A6a-r.
Fourteen of those 16 gains had a positive net mapped-gold change; 15 cited a
mapped gold resource. Six were abstention-to-correct and ten were
wrong-to-correct. The single loss was an empty-gold, last-event overclaim.

### Traversal versus vocabulary

Traversal fetched 159 resources across 22 dispatched questions, but only 36
were mapped-gold occurrences across nine questions. One question contributed
17 of the 36 gains and the top three contributed 27, giving the added resources
a **22.6% mapped-gold yield**.

All six correctness differences occurred among the nine questions with a gold
gain. Five favored traversal and one favored vocabulary. Four favorable flips
were abstention-to-correct and one was wrong-to-correct. In the loss, retrieval
improved from 2/26 to 19/26 mapped-gold occurrences, but the answerer selected
an older root for a “last” question. Retrieval worked; the flat packet did not
bind linked evidence to a ranked event.

After vocabulary, 14 of 19 remaining errors already had partial or full mapped
gold evidence in the packet. Across the 32 mapped-gold questions, nine wrong
answers had gold present but cited none, and five cited gold yet still answered
incorrectly. The next bottleneck is therefore not only recall: it is salience,
event grouping, temporal ordering, aggregation, and knowing when the packet is
insufficient.

## Artifact limitations

- The sealed bundle's current hashes match its manifest, but the files are
  ordinary mode-0644 files. The chain detects drift; it is not protection
  against a malicious actor rewriting both artifacts and manifest.
- Panel prompts and aggregate verdicts were retained and hashed, but the
  panel's raw event streams were not retained. Its no-tool claim is therefore
  weaker than the corresponding answer-call audit.
- The harness does not retain hidden chain-of-thought. The audit inspected only
  visible structured messages, evidence summaries, citations, and event types.
- The public aggregate excludes question, patient, and resource identifiers by
  design. Authorized reviewers need the sealed private artifact directory to
  replay answer-level checks.

## Protocol changes for A11

1. Distinguish top-level-resource citations from reference-only citations in
   the answer schema and grader.
2. Freeze a benign-stderr warning classifier before the run.
3. Make the bundle read-only, sign the manifest, and anchor its digest outside
   the experiment host before answering.
4. Retain and hash panel event streams with the same no-tool audit used for
   answer calls.
5. Add a hidden-gold manipulation check only after all answers are frozen.
6. Report resource yield and cap allocation by path family, not just total
   traversal recall.
