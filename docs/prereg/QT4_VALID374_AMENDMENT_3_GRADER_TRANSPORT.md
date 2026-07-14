# QT-4 valid374 Amendment 3 — valid374 finalizer compatibility

**Registered:** 2026-07-13 PT, after all 1,122 v2 answers completed and before
deterministic grading, panel grading, answer-content inspection, or correctness
inspection.

**Status:** binding implementation amendment. It makes the inherited
micro-screen analysis code execute the already-registered valid374 protocol.
It does not change sealed inputs, packets, answers, deterministic grading
rules, estimands, uncertainty method, panel judge configuration, fixed-sequence
rules, or promotion thresholds.

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

Pre-result review then found three more inherited micro-screen assumptions that
would fail closed or report the wrong estimand: a default count of 42, pooled
contrasts in place of the frozen 44-question dispatched stratum, and the old
screen-only promotion message. The panel cache also stored votes without
cryptographically binding each accepted batch receipt to those votes. These
are finalizer implementation gaps, not changes to the registered analysis.

## Binding compatibility repair

1. The grader accepts only the four v2 identifiers above.
2. Every accepted completion must include an existing, byte-empty
   `stderr.log`; the grader recomputes its audit and SHA-256 and compares both
   with the sealed receipt.
3. Every archived failed attempt must include `stderr.log`; the grader
   recomputes its audit and SHA-256 and compares both with the append-only
   receipt before counting its token usage.
4. The controller profile binds the exact 374-question schedule, frozen 44/330
   strata, preregistered question-spec and holdout CSV SHA-256s, rotating arm
   order, `gpt-5.6-sol`/`high` execution, and complete v3 snapshot inventory.
   The grader no longer accepts the inherited 42-question default for this
   profile.
5. The registered inferential contrasts use only the 44 frozen dispatched
   questions. Pooled 374-question and 330-question negative-control accuracy
   remain separate one-point degradation gates. H2 is tested only if every H1
   gate passes. Vocabulary uses positive net gold-occurrence change; traversal
   requires at least one gain and zero losses.
6. The panel judge prompt, model, effort, votes, batch size, timeout, binary,
   and version remain unchanged. Each accepted round/batch receipt now binds
   its exact opaque IDs and returned verdict hash, and the finalizer requires
   exact receipt coverage for every cached vote.
7. The final report exposes the registered strata, abstention, deterministic
   versus panel routing, verified judge configuration, mechanism outcomes, and
   accepted/all-attempt economics. Aborted-v1 tokens remain a separately
   labeled protocol-overhead receipt and are never mixed into v2 correctness or
   economics.
8. Any nonempty accepted stderr, receipt mismatch, missing archive,
   unregistered profile/transport, wrong stratum, unbound panel vote, or
   fixed-sequence inconsistency fails closed.

The analysis version is incremented to identify this compatibility finalizer.
All statistical and promotion conditions come directly from
`QT4_VALID374_HOLDOUT.md`; this amendment supplies the missing implementation
and integrity bindings without selecting a result-dependent rule.
