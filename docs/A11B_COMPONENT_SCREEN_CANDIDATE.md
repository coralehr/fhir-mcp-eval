# A11b component-screen candidate

Status: **zero-model implementation candidate; not preregistered, sealed, or
authorized for answer calls**.

This package provides fixtures intended to address the attribution ambiguity
tracked in issue #72. The current T1 treatment adds temporal ranks, a
selected-event marker, and an answerability receipt together; E1 then
duplicates flat path citations with event groups. A future development screen
must isolate those changes before an efficacy treatment is selected.

## Deterministic packet fixtures

`a11b_component_screen.compile_component_screen` consumes the governed source
packet, raw question, and question-only plan. It independently reruns
`a11b_event_compiler.compile_arms`; it does not trust a caller-supplied compiled
arm, temporal aid, group, or receipt. It emits:

1. `t0`: shared resources and path citations only.
2. `temporal_rank_only`: T0 plus root identifiers and precomputed ordinal/tie
   ranks. It excludes selected markers, answerability, requirements, and
   duplicated event timestamps.
3. `selected_marker_only`: T0 plus the existing per-event selected boolean and
   root reference, without changing the T1 nesting or field names.
4. `answerability_receipt_bundle`: T0 plus the complete deterministic receipt
   bundle. Any effect is attributable to that bundle, not its individual state,
   count, or reason fields.
5. `path_only`: the current T1 representation, with temporal aids and flat path
   citations but no event groups.
6. `group_only`: the same resources and temporal aids represented with typed
   groups, without duplicated flat path citations.
7. `t0_byte_matched_placebo`: T0 plus semantically empty ASCII padding whose
   canonical JSON byte count exactly matches `path_only`.

Every arm has a canonical SHA-256/byte receipt, shares the compiler's governed
evidence, respects one packet bound, binds the upstream compiler receipt, and
records `model_calls=0`. Recompilation rejects forbidden or gold-bearing input,
question-plan drift, invalid FHIR reference paths, oversized packets, and
multiple selected events.

The placebo is a byte-equivalence fixture only. It is not the token-matched
causal control required by issue #72: neutral content, the exact model tokenizer,
and the contrast-specific target size must be frozen in a later preregistration.

These fixtures are not a schedule. A later preregistration must choose the
development subset, token-matched contrasts, model configuration, multiplicity
policy, and promotion threshold before any call.

## Grounding endpoint

`a11b_grounding_metrics.compile_grounding_report` deterministically joins
auditor-only gold, accepted structured answers, exact registered question/arm
identities, and canonical model packets whose hashes match separately registered
packet receipts. Visible resource references are derived from those packets;
callers cannot self-declare visibility. It reports per question and per arm:

- exact-answer correctness;
- answerability-state correctness;
- selected-terminal hits, any selected-path-reference hits, and full selected
  path coverage;
- citation precision and recall as integer numerator/denominator receipts;
- invalid-citation rate;
- correctness jointly supported by a selected-terminal citation; and
- correct but unsupported answers as a separate `unsupported_correct` metric.

Future corpus gold therefore carries
`selected_terminal_resource_ref` in addition to `selected_path_refs`. Both are
audit-only and are rejected from model-visible artifacts. For answerable cases,
the selected terminal must belong to the registered selected path. Unanswerable
cases carry a null terminal and an empty selected path.

The joint endpoint deliberately prevents an exact lucky guess from being
reported as grounded. It does not use an LLM judge and does not inspect
reasoning traces.

## Remaining issue #72 work

This candidate does not complete issue #72. Before efficacy:

- run the seven-arm screen only after a reviewed preregistration and sealed
  controller;
- build the model/tokenizer-pinned neutral token-matched controls; the current
  padding fixture proves canonical bytes only;
- add independent reliability trials and pass@1/pass^k reporting;
- freeze exactly one winning treatment against T0 on a new patient-disjoint
  efficacy split;
- add the registered counterfactual corruptions and evidence-position
  counterbalancing; and
- select and seal an independent-family panel only for outcomes that the
  deterministic endpoint cannot grade.
