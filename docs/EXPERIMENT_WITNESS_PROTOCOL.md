# Experiment attempt witness

Status: protocol core implemented; no live answer or panel runner uses it yet.

## Why this exists

The 2026-07-15 adversarial sweep found that local `chmod` and a local JSONL
ledger do not make experiment attempts append-only. The filesystem owner can
restore write permission on a parent, move a read-only attempt directory out of
the inventory, truncate the local ledger, and resume with a reset attempt count.
The same attack can remove an accepted answer or panel vote and cause another
model call.

Read-only local files remain useful protection against accidental edits. They
are not an integrity boundary against their owner, so future experiments must
not call them immutable or append-only.

## The module and its interface

`experiment_witness.py` is a deep module with three operations:

- `open_call(descriptor, expected_head)` reserves exactly one scheduled call;
- `close_call(...)` binds its terminal outcome, opaque artifact commitment, and
  reconciled token usage;
- `status()` replays and verifies the complete signed chain.

The implementation hides schedule enforcement, attempt caps, hash chaining,
Ed25519 receipt signing, signature verification, idempotent lost-ack recovery,
exclusive sequence slots, file and directory `fsync`, and HEAD reconstruction.
Calls carry only phase, ordinal, attempt number, and domain-separated HMAC-SHA256
commitments made with at least 256 bits of witness/executor-owned secret key
material. Raw SHA-256 is forbidden for public commitments because codes, dates,
and other low-entropy clinical values are dictionary-guessable. Receipts do not
carry prompts, answers, question or patient identifiers, paths, resources, or
raw clinical-data hashes.

The witness public key, run ID, exact schedule commitments, controller digest,
witness-host identity, and pinned OpenSSH signing/verifying executable digest
must be part of the independently approved pre-answer anchor. The private key
must never exist on the run host.

## Required deployment seam

The ledger must run under a principal the experiment runner cannot mutate. The
preferred first deployment is a persistent witness process on a separate machine,
reached through a restricted SSH command. Persistence lets each transition extend
the already verified in-memory chain; startup and explicit audit replay every
signature. A dedicated same-host account is acceptable only if the run account
has neither `sudo` nor any path to that account, its key, ledger, or model
credential.

For the stronger call-count claim, the witness must also own the Codex credential
and launch the pinned binary itself. Merely signing caller-submitted receipts
prevents post-hoc deletion but cannot prove that the caller did not make an
unwitnessed call or fabricate a local artifact commitment.

The live sequence is:

1. The witness independently verifies the commit-pinned external anchor and its
   own anchored public key, run ID, schedule, inputs, and runtime.
2. Under one witness lock it verifies the signed chain and durably appends a
   `call_opened` receipt before spawning Codex.
3. It captures the attempt into staging, validates the registered no-tool and
   transport rules, `fsync`s every artifact and directory, and computes the
   opaque artifact-root commitment.
4. It durably appends a signed `call_closed` receipt before acknowledging the
   runner. The runner may keep a local mirror, but witness state is authoritative.
5. Before any later answer or panel call, the next transition must extend the
   exact witnessed head. Missing local mirrors block audit but never reset the
   remote attempt count.

## Fail-closed crash rules

- A repeated identical open or close after a lost acknowledgement returns the
  original signed receipt and creates no new event.
- An open reservation with no conclusive captured outcome is `indeterminate` and
  aborts the run. It is never silently retried.
- A conflicting close, stale externally retained head, skipped sequence,
  schedule substitution, signature failure, retry-cap breach, or witness outage
  blocks all new model calls.
- HEAD is only a cache. Startup reconstructs a missing or strict-prefix HEAD from
  the contiguous signed event chain; a divergent cached HEAD is an integrity
  failure.

The hash chain is tamper-evident, not self-witnessing. If an attacker can delete
both a signed tail and every independent record of its acknowledged head, a
shorter valid prefix cannot prove that the tail once existed. Deployment must
therefore make the witness store non-rollbackable to the run principal and keep
each acknowledged head on the independent witness/checker side. S3 Object Lock
or another WORM replica is required if the witness-host administrator is also in
the adversarial scope.

## Compatibility and current evidence

A11 controller v1/v2/v3 artifacts remain readable under their frozen historical
rules. They are not retroactively reclassified as witnessed runs, and legacy
controllers remain live-disabled where already required.

This finding does not show that the completed QT-4 or A11 runs were altered. Their
published forensic audits found coherent schedules, receipts, event streams,
token reconciliation, and no observed evidence of resampling. It narrows the
claim: those checks provide strong procedural and forensic evidence, not
cryptographic proof against a malicious owner of the run filesystem.

No A11b answer or panel call may start until a new controller version binds a
deployed independent witness/executor and the full fake-executor adversarial
suite passes with zero model calls.
