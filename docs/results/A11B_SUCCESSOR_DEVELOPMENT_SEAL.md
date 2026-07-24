# A11b successor development seal

Status: **sealed candidate awaiting independent exact-head approval**.

Date: 2026-07-18

Answer-model and judge calls at seal: **0**

## What is sealed

The successor development controller binds exactly 64 patient-disjoint
development questions and three rotating arms for 192 answer calls. The
reserved 384-patient efficacy split remains unmaterialized. Grading is the
registered deterministic exact-alias endpoint and explicitly authorizes zero
panel calls.

Two independent clean-root development corpora (`c` and `d`) produced
byte-identical public and audit manifests. The replacement installation package
was built twice from source commit
`cfd673f472b54e99de37a109097ea5109c3d7c21` and every package byte matched.
Controllers compiled independently from corpus builds `c` and `d` were also
byte-identical.

Key public receipts:

- controller SHA-256:
  `8daf6bac251cef8ba0bed4bf87a17295321b9825ebab97722e89b165d4a7f2ba`;
- private executor bundle SHA-256:
  `1c03d5e08585a1c4dbb202e4e4d6a93fef6654fe36bea1a146de72c66cb6dfa5`;
- installation manifest SHA-256:
  `faf90b819959f7f58898c0be465386ac1317364a72b5c6799732bf91bf7442e8`;
- external anchor request SHA-256:
  `90165a0f1f63f08a4bf79d6651454cedbec7aceb856fd1f3233d865388ab73ee`;
- run ID:
  `d948ff16474f5de8822e8ac065bd953068326a783d283123ededb266f4897163`.

The machine-readable receipt is
[`a11b-successor-artifacts/development-seal-receipt.json`](a11b-successor-artifacts/development-seal-receipt.json),
and the public anchor candidate is
[`../../anchors/a11b-successor-development-controller-2026-07-18.json`](../../anchors/a11b-successor-development-controller-2026-07-18.json).

## Authoritative release checklist

This is the authoritative completed/pending checklist for the successor
development release. Older planning checklists describe component readiness,
not permission to run.

- [x] Pin the successor Synthea generation specification and reproduce two
  clean 448-Patient generation roots. The generation receipt SHA-256 is
  `acb5ad3ba2ba8032507d69afc8375d181dc49376392e39564343490f718df0d8`;
  see the
  [`A11B_SUCCESSOR_ZERO_MODEL_BUILD.md`](A11B_SUCCESSOR_ZERO_MODEL_BUILD.md)
  evidence record.
- [x] Build two byte-identical, patient-disjoint 64-Patient development
  corpora without materializing the reserved 384-Patient efficacy split. The
  public and audit manifest SHA-256 values are
  `9bf09379d93db80c430b59a59ca79f522e185de6baef048bed40f29017f3e74d`
  and
  `b233b4bdfe9411ccf2720acd3e7850a01f340b73bce46bdc07adda0260362dcc`.
- [x] Independently rebuild the controller and installation package with
  byte-identical outputs. Their SHA-256 values are listed above and bound in
  the machine-readable development-seal receipt.
- [x] Exercise the original sealed package through exact-head verification and
  the real macOS bootstrap. Those pre-readiness attempts exposed an
  output-inventory mismatch, a retry-hostile fixed transport-parent creation
  path, and a hidden GitHub membership false negative. The replacement package
  fixes each defect with a regression test. The retired controller made zero
  model calls and no answer content was opened.
- [x] Bind deterministic exact-alias grading, 192 answer calls, zero panel
  calls, the native runtime, the trusted executor, and the witness schedule in
  the public anchor candidate.
- [x] Pass the deterministic suite and macOS installer-protocol CI lanes on the
  exact candidate head.
- [ ] Receive an independent trusted APPROVED review on the exact final PR
  head, then merge that same head without modification.
- [ ] Verify the merged anchor through its commit-pinned GitHub Contents API
  URL and produce the signed external-anchor verification receipt.
- [ ] Complete the transactional content-free readiness handshake with zero
  model calls.
- [ ] Run and close all 192 development answer calls. Do not materialize or
  open the reserved efficacy split unless both registered development
  contrasts show nonzero paired correctness discordance.

## Interpretation boundary

A11b is a controlled synthetic mechanism experiment. The benchmark generator
creates the clinical facts, timestamps, aliases, paths, and audit-only gold
answers that define each question. Synthea primarily supplies synthetic Patient
identity and sanitized background/noise resources around those constructed
paths.

A successful development result can therefore support the narrow claim that a
registered representation helps a model retrieve or use deliberately hidden
path evidence under this synthetic contract. It cannot establish retrieval from
organic production EHR records, real-world clinical validity, production
authorization or correction behavior, universal graph superiority, or the
need for a native graph database. Those require separate natural-chart,
principal-varying, and production-substrate evaluations.

## Retired zero-call candidate

The previously published controller
`daca25e7ed4efbb1a7c27c8d3f1e79fa19bfd5adfddb41e619ff223018c68478`
was independently approved and merged, but the transactional macOS bootstrap
rejected it before readiness. No answer-model or judge call was released. It
is retained only as failure evidence and must not be launched or reinterpreted
as part of this replacement run.

## Remaining gate

This candidate does not authorize a model call. An independent trusted reviewer
must approve the exact final PR head before merge. After merge, the external
anchor verifier must succeed against the commit-pinned anchor URL. Only then may
the transactional installer perform the content-free zero-call readiness
handshake and release the 192-call development run.
