# QT-4 valid374 Amendment 2 — transport separation and full restart

**Registered:** 2026-07-13 PT, after the v1 transport-integrity hard stop and
before any v2 answer execution or inspection of v1 answer content.

**Status:** binding protocol amendment. It changes transport and controller
identity only. The frozen question set, packets, model, reasoning effort,
grading rules, estimands, and promotion thresholds remain unchanged.

## Why this amendment exists

The first sealed valid374 controller completed 20 paired triplets. On the next
QT-4T attempt, the integrity audit found one non-JSON line inside the stream
that the harness had recorded as Codex JSONL. The attempt was quarantined and
the controller stopped. No answer content, correctness label, or per-question
answer was inspected.

Code review found that the v1 harness configured the Codex subprocess with
`stderr=subprocess.STDOUT`. That made a CLI diagnostic on stderr
indistinguishable from a malformed JSONL event. The archived attempt remains
contaminated under the registered v1 rules and is not reinterpreted.

## Binding v2 transport rules

1. Codex stdout and stderr are written to separate files.
2. `events.jsonl` contains stdout only and remains subject to the existing
   strict JSONL, sequence, tool-use, UTF-8, and terminal-newline audit.
3. `stderr.log` is recorded separately with its byte count, SHA-256, UTF-8
   validity, and terminal-newline status. Its content is not copied into
   receipts or reports.
4. An accepted answer requires an existing, byte-empty `stderr.log`. Any
   nonempty stderr rejects the attempt even when Codex exits zero and stdout is
   otherwise valid. The rejected answer is never adopted.
5. No malformed stdout line is ignored, reclassified, or made retryable by this
   amendment. Existing strict contamination behavior remains in force.
6. Timeout and local OS diagnostics are written to stderr, never synthesized
   into the model event stream.
7. Attempt receipts bind both event-log and stderr-log hashes. Resume validates
   both archived streams byte-for-byte.
8. The v2 controller uses a new immutable manifest, bootstrap bundle, output
   directories, and transport identity
   `separated-stdout-jsonl-stderr-v2`.

## Restart and analysis boundary

The v1 controller is declared **aborted pre-analysis**. Its 60 accepted outputs
(20 per arm) and the quarantined attempt are excluded from correctness,
uncertainty, recall/mechanism, and accepted-answer economics. They will not be
copied, resumed, or mixed into v2.

V1 token consumption is reported separately as aborted-protocol overhead. V2
restarts the complete 374-question schedule for all three arms (1,122 accepted
answers before registered grading), subject to the existing retry cap and
integrity rules.

The registered v2 analysis remains:

- primary: QT-4V minus A6a-r correctness;
- secondary: QT-4T minus QT-4V correctness;
- deterministic recall and mechanism outcomes;
- accepted-answer and all-attempt token economics;
- the same uncertainty method, blinded panel, and promotion thresholds frozen
  in [`QT4_VALID374_HOLDOUT.md`](QT4_VALID374_HOLDOUT.md).

No valid374 outcome is claimed until the new sealed controller completes and
the registered deterministic grading and arm-blind panel are finished.
