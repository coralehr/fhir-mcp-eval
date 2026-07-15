# A11 governed retrieval and source-version gate

Status: **passed before answer calls; dataset seal also passed**

Completed: 2026-07-15

## Decision

A11 T and E must consume one immutable traversal source. They may not issue
independent graph walks or synthesize authorization after retrieval. The new
`a11-governed-retrieval-v1` factory consumes an adapter-verified V bundle, an
independently pinned synthetic source snapshot, and an explicit benchmark
policy artifact whose canonical bytes and hash were pinned independently. It
derives roots only from V and stores the traversal source as canonical
immutable bytes.

This is deterministic benchmark governance over synthetic data. It is not a
Bonfire authorization implementation and does not prove production ABAC.

## Zero-model result

The gate binds:

- recipe, adapter, producer manifest, record, packet, V rendering and root
  hashes;
- hashed principal, practice, patient, purpose and allowed-purpose context;
- the independently pinned policy-artifact hash;
- source identity, exact source version and independently pinned snapshot;
- every included resource's canonical reference and `meta.versionId`;
- question ID, question text and question-plan hashes;
- full replayable RFC 6901 path citations, traversal bounds and one shared
  retrieval-source hash; and
- separate T-flat and E-grouped model-packet hashes derived from that same
  retrieval source.

Available `Resource/id/_history/version` requests retain requested and resolved
references separately. An unavailable historical version never falls back to
the current resource. Purpose denial, snapshot tampering, missing versions,
duplicate identities, explicit-patient-binding failures, practice scope
failures and model-packet byte overflow fail closed. The factory exposes the
full requested/resolved path source only through an audit-specific fresh-copy
view. The answering runner must use the question-bound immutable UTF-8 payload
API; mutable parsed packet views are audit/test-only. Every public packet load
checks the canonical bytes against the receipt, and callers can pin the exposed
receipt hash across process boundaries. Non-standard non-finite JSON numbers
fail before hashing. Unavailable target identities and denial reasons remain outside
model-visible packets and are represented in the outer receipt only by bound
hashes.

The input envelope is also bounded before traversal: a per-question snapshot
is limited to 16 MiB, 4,096 resources and 256 V roots. The traversal reuses the
validated read-only source list instead of deep-copying the full snapshot;
target, edge, path and final packet caps remain independently enforced.

## Downstream closure

The deterministic multi-family corpus was subsequently built twice with
byte-identical output and passed the exact family, depth, split, shortest-path,
leakage, quota, producer, adapter, and governed-retrieval preflight checks. Its
manifest SHA-256 is
`442ca8d204fbd81f06e0abaf2ea5022b375deabb71a93d5ebaeccef98e99fe3c`.
See [`A11_DATASET_GATE.md`](A11_DATASET_GATE.md). Answer calls remain blocked
until the separate registered controller manifest is sealed; this remains
benchmark governance over synthetic data, not a claim about production
Bonfire ABAC.

## Reproduction

```bash
python3 -m unittest tests.test_a11_governed_retrieval -v
```
