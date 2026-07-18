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

## Remaining gate

This candidate does not authorize a model call. An independent trusted reviewer
must approve the exact final PR head before merge. After merge, the external
anchor verifier must succeed against the commit-pinned anchor URL. Only then may
the transactional installer perform the content-free zero-call readiness
handshake and release the 192-call development run.
