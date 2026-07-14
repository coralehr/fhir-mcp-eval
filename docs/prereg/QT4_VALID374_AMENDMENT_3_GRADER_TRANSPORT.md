# QT-4 valid374 Amendment 3 — grader receipt compatibility

**Registered:** 2026-07-13 PT, after all 1,122 v2 answers completed and before
deterministic grading, panel grading, answer-content inspection, or correctness
inspection.

**Status:** binding implementation amendment. It changes only the grader's
integrity validation for the already-registered v2 transport receipts. It does
not change sealed inputs, packets, answers, grading rules, estimands,
uncertainty, panel configuration, or promotion thresholds.

## Why this amendment exists

The v2 answer controller completed 374 clean accepted answers in each of the
three arms. Before opening any correctness output, the registered deterministic
grader failed closed because its receipt validator still required the earlier
QT-4 micro-screen controller and attempt schema identifiers. The sealed v2
controller correctly uses the identifiers and transport required by Amendment
2:

- controller kind `qt4_interleaved_controller_manifest`;
- controller schema `qt4-controller-v3`;
- attempt schema `qt4-attempt-v3`; and
- transport identity `separated-stdout-jsonl-stderr-v2`.

The mismatch is confined to integrity validation at the analysis boundary. It
does not affect answer execution or any scoring rule.

## Binding compatibility repair

1. The grader accepts only the four v2 identifiers above.
2. Every accepted completion must include an existing, byte-empty
   `stderr.log`; the grader recomputes its audit and SHA-256 and compares both
   with the sealed receipt.
3. Every archived failed attempt must include `stderr.log`; the grader
   recomputes its audit and SHA-256 and compares both with the append-only
   receipt before counting its token usage.
4. Existing prompt, packet, event-stream, answer, usage, retry-ledger, model,
   reasoning-effort, and controller-snapshot validations remain unchanged.
5. Any nonempty accepted stderr, receipt mismatch, missing archive, or
   unregistered transport identity fails closed.

The analysis version is incremented only to identify this validator repair.
All registered deterministic scoring, arm-blind panel grading, paired
statistics, cluster bootstrap uncertainty, economics, and promotion logic are
unchanged.
