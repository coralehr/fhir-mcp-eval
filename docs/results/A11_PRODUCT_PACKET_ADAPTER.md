# A11 promoted product-packet adapter gate

Status: **implementation gate passed on synthetic non-PHI product records;
sealed efficacy corpus and governed authorization receipt still pending**

Completed: 2026-07-14

## What is now proven

`a11_packet_adapter.py` opens the complete `compile_evidence.py` JSONL file and
its manifest once, then adapts any selected record from that verified bundle.
It does not accept an unbound packet dictionary. Before producing an A11 V
payload it verifies:

- the manifest SHA-256 against a value supplied independently by the caller;
- the whole JSONL SHA-256 against `manifest.output.sha256`;
- unique question IDs, exact question coverage, and manifest count;
- the promoted `qt4-vocabulary-promoted-v1` recipe;
- question-only planner `qo-v2.1`, `micro-vocab` only, live rather than
  plan-only packets, and no traversal in V;
- the packet's recomputed internal SHA-256 and its manifest entry;
- strict allowlisted manifest, record, intent, and nonclinical packet metadata
  schemas, plus rejection of known answer/gold/label fields and prefixes;
- a question-only A11 plan derived before any answer inputs;
- at least one registered root that explicitly names the requested patient;
  and
- hashes for the adapter, packet builder, product entrypoint, answer harness,
  A11 evidence core, and event-group compiler.

The returned V object is exactly
`codex_harness.model_visible_packet(record["packet"])`. Its stored JSON string
is exactly `codex_harness.render_model_visible_packet(record["packet"])`, the
same indent-two, sorted UTF-8 fragment embedded in the answering prompt. The
adapter does not call the synthetic `project_star`, does not add an empty
`path_citations` member, and does not pass V through the proxy byte-bound
replacement. Tests compare the literal bytes and their SHA-256.

The JSONL bytes are read once, hashed, and parsed from the same immutable buffer;
duplicate JSON keys and corpus/record size overruns fail closed. One verified
`PromotedBundle` supports all selected questions without reparsing the corpus.
The regression suite also proves that whole-file tampering, a wrong pinned
manifest, duplicate question IDs, non-dispatched rows, mutated question-only
intent/query plans, patient-ambiguous selected resources, direct or nested
cross-patient `Patient/` references, and forbidden benchmark metadata fail
closed. Returned packets are detached from the verified bundle, so caller
mutation cannot contaminate later loads. No answer or judge model is called.

## What is not yet proven

The integration test runs the actual `compile_evidence.py` command entrypoint
against a local synthetic FHIR client and passes its real non-plan-only JSONL
and manifest through the adapter. It has not yet been run against the future
efficacy corpus because that corpus does not exist.

The strict nonclinical schemas are not a proof that arbitrary FHIR clinical
fields contain no gold aliases. The caller must independently pin the source
dataset, question specification, deterministic extractor/configuration, and
manifest hash. The adapter's integrity receipt proves consistency with that
outer pin; it does not establish source legitimacy by itself.

More importantly, current product records do not carry a governed principal,
practice, purpose, consent/allowed-purpose set, immutable source version, or
authorization receipt. They also project away `meta.versionId`, so an exact-
version traversal cannot be proven from the V packet alone. The adapter
therefore exposes deterministic root candidates but does not relabel them as
authorized roots.

There is also a producer-level feasibility blocker outside the adapter:
`qo-v2.1` does not emit `DiagnosticReport` searches for microbiology questions.
The exact promoted V path therefore cannot currently generate roots for two of
the four registered A11 families. A dated pre-answer planner/protocol amendment
and a new sealed producer receipt are required before efficacy.

Before T/E efficacy, a governed, non-model-visible source receipt must bind:

- principal, practice, purpose, patient, and allowed purposes;
- immutable source/snapshot version;
- authorized root refs derived from the sealed V resources, never a
  benchmark-provided seed list;
- the version-preserving traversal snapshot; and
- one immutable retrieval result consumed identically by T and E.

E must receive only that retrieval result plus the question-only plan. It may
not receive the source resolver, source snapshot, benchmark case, answer, or
gold fields.

## Tests

```bash
python -m unittest tests.test_a11_packet_adapter
```

This gate removes the packet-rendering ambiguity that made the synthetic V
proxy insufficient. It does not remove the separate authorization and dataset
gates described in [`A11_EVENT_GROUP.md`](../prereg/A11_EVENT_GROUP.md) and
[`A11_SUBSTRATE_AUDIT.md`](A11_SUBSTRATE_AUDIT.md).
