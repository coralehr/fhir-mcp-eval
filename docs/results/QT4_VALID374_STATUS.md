# QT-4 valid374 untouched-holdout status

Status updated: 2026-07-13 19:10 PT

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

The 60 accepted outputs from this aborted controller will not be reused in any
efficacy or economics analysis. Any tokens consumed by the controller will be
reported only as aborted-protocol overhead, never as arm economics.

Amendment 2 now preregisters the separated transport and a full fresh 374-by-3
restart. A synthetic preflight produced four strict JSONL events and a
byte-empty stderr stream. The v2 controller then launched from zero under
manifest SHA-256
`ed5e27e2de7cbec71caf5cefe1d1d8c90c7ba2250e1c494ded76d0e3fde15605`,
with `gpt-5.6-sol` at high reasoning effort, the frozen 374-question schedule,
and new controller/output namespaces. It is running; no holdout outcome exists
yet. Final correctness, uncertainty, recall, accepted/all-attempt answer
economics, and separately metered panel-judging tokens will be published only
if that complete run and the registered arm-blind panel pass their integrity
checks.
