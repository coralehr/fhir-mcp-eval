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
byte-identical public and audit manifests. The installation package was built
twice from source commit
`01e42ead1c0787df25aca65199d8eb80050b02fe` and every package byte matched.
Controllers compiled independently from corpus builds `c` and `d` were also
byte-identical.

Key public receipts:

- controller SHA-256:
  `daca25e7ed4efbb1a7c27c8d3f1e79fa19bfd5adfddb41e619ff223018c68478`;
- private executor bundle SHA-256:
  `7e386659a0c000ab22d6fc9954235c23dc74beec885ebcbe6562ce766dcfcb48`;
- installation manifest SHA-256:
  `d8d5ebcfab34b06b8b5021df560679040768f95652b0f48f6275ae283d17f026`;
- external anchor request SHA-256:
  `8ce6678978da6a843cd94e8e92dc668c2f0240f5cc91897bdaa12a78f4b68843`;
- run ID:
  `8005f860fdebcbab717e62a7bea35291b6570769d7091db00712a896e2afa034`.

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

## Remaining gate

This candidate does not authorize a model call. An independent trusted reviewer
must approve the exact final PR head before merge. After merge, the external
anchor verifier must succeed against the commit-pinned anchor URL. Only then may
the transactional installer perform the content-free zero-call readiness
handshake and release the 192-call development run.
