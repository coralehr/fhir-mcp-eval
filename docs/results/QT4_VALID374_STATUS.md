# QT-4 valid374 untouched-holdout status

Status updated: 2026-07-14 00:20 PT

**Final status: complete.** The fresh v2 controller produced 1,122/1,122
accepted sealed answers, passed deterministic grading and the pinned arm-blind
panel, and yielded `promote_vocabulary_only`. The registered 44-question
microbiology contrast was 10/44 for A6a-r, 25/44 for vocabulary, and 29/44 for
vocabulary plus traversal. Vocabulary versus A6a-r was +34.1 points
(patient-cluster 95% interval +17.9 to +50.0, exact McNemar p=.000275).
Traversal versus vocabulary was +9.1 points (interval 0.0 to +20.9,
p=.21875), so traversal was not promoted.

The authoritative aggregate public report is
[QT4_VALID374_RESULT.md](QT4_VALID374_RESULT.md); the post-result no-cheating
and mechanism review is
[QT4_VALID374_FORENSIC_AUDIT.md](QT4_VALID374_FORENSIC_AUDIT.md). The remainder
of this file preserves the operational history of the aborted v1 controller.

The first sealed 374-question valid-split holdout controller is aborted
pre-analysis. It hard-stopped after 20 clean paired triplets (60 accepted
outputs) and one additional vocabulary-plus-traversal attempt was quarantined
by the transport-integrity check. No answer content or correctness was
inspected before or after the stop, so this run produces no holdout outcome.

The first launch attempt failed before manifest creation or any model call
because the command named the repository harness instead of the immutable
bootstrap copy. The registered status check then reproduced the zero-model gate
from the sealed relative paths with 374 scheduled questions and zero attempts.
The controller was relaunched with the bootstrap harness; it created the sealed
manifest and began the balanced run. This was an operational launch-path repair,
not a change to packets, questions, model configuration, or analysis. The later
transport-integrity failure was a separate hard-stop condition: the original
harness combined model-process stdout and stderr into the strict JSONL event
stream, and the quarantined attempt contained one non-JSON line.

The 60 accepted outputs from this aborted controller were not reused in any
efficacy or economics analysis. Its 3,546,961 completed-turn tokens are
reported only as aborted-protocol overhead, never as arm economics.

Amendment 2 now preregisters the separated transport and a full fresh 374-by-3
restart. A synthetic preflight produced four strict JSONL events and a
byte-empty stderr stream. The v2 controller then launched from zero under
manifest SHA-256
`ed5e27e2de7cbec71caf5cefe1d1d8c90c7ba2250e1c494ded76d0e3fde15605`,
with `gpt-5.6-sol` at high reasoning effort, the frozen 374-question schedule,
and new controller/output namespaces. It completed all arms, deterministic
grading, and 120 accepted panel calls without an invalid current receipt. The
final result publishes correctness, uncertainty, recall, accepted/all-attempt
answer economics, panel tokens, and the separately labeled v1 overhead.
