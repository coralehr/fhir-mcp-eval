# A11 versioned product-packet adapter gate

Status: **implementation gate passed on synthetic non-PHI product records;
sealed efficacy corpus and governed authorization receipt still pending**

Completed: 2026-07-14

## What is now proven

`a11_packet_adapter.py` v2 opens the complete `compile_evidence.py` JSONL file and
its manifest once, then adapts any selected record from that verified bundle.
It does not accept an unbound packet dictionary. Before producing an A11 V
payload it verifies:

- the manifest SHA-256 against a value supplied independently by the caller;
- the whole JSONL SHA-256 against `manifest.output.sha256`;
- unique question IDs, exact question coverage, and manifest count;
- either the historical `qt4-vocabulary-promoted-v1` contract or the explicit
  pre-answer `a11-four-family-v1` contract selected independently by the
  caller;
- the recipe-bound question-only planner (`qo-v2.1` or
  `qo-v2.2-a11-four-family`), `micro-vocab` only, live rather than plan-only
  packets, and no traversal in V;
- the packet's recomputed internal SHA-256 and its manifest entry;
- strict allowlisted manifest, record, intent, and nonclinical packet metadata
  schemas, plus rejection of known answer/gold/label fields and prefixes;
- a recipe-bound question-only A11 plan derived before any answer inputs;
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
The adapted envelope also carries the independently validated evidence-recipe
ID, so downstream V/T/E assembly does not have to infer recipe identity from a
planner string.

The JSONL bytes are read once, hashed, and parsed from the same immutable buffer;
duplicate JSON keys and corpus/record size overruns fail closed. One verified
`PromotedBundle` supports all selected questions without reparsing the corpus.
The regression suite also proves that whole-file tampering, a wrong pinned
manifest, duplicate question IDs, non-dispatched rows, mutated question-only
intent/query plans, patient-ambiguous selected resources, direct or nested
cross-patient `Patient/` references, ambiguous absolute paths with multiple
Patient segments, and forbidden benchmark metadata fail closed. Returned
packets are detached from the verified bundle, so caller
mutation cannot contaminate later loads. No answer or judge model is called.

## What is not yet proven

The integration tests run the actual `compile_evidence.py` command entrypoint
against a local synthetic FHIR client and pass its real non-plan-only JSONL
and manifest through the adapter. Adapter v2 proves that the explicit A11
recipe yields Observation roots for Observation/default questions and
DiagnosticReport roots only for explicitly worded DiagnosticReport questions,
with all four registered question-plan shapes reachable. It has not yet been
run against the future efficacy corpus because that corpus does not exist.

The adapter now rejects benchmark-only keys and prefixes recursively inside
the registered Observation/DiagnosticReport roots as well as packet metadata.
That is not proof that arbitrary clinical *values* contain no answer aliases.
The caller must independently pin the source
dataset, question specification, deterministic extractor/configuration, and
manifest hash. The adapter's integrity receipt proves consistency with that
outer pin; it does not establish source legitimacy by itself.

More importantly, current product records do not carry a governed principal,
practice, purpose, consent/allowed-purpose set, immutable source version, or
authorization receipt. They also project away `meta.versionId`, so an exact-
version traversal cannot be proven from the V packet alone. The adapter
therefore exposes deterministic root candidates but does not relabel them as
authorized roots.

The former producer-level feasibility blocker is resolved by the dated
pre-answer `a11-four-family-v1` amendment. Historical `qo-v2.1` remains
Observation-only and reproducible; the A11 recipe binds
`qo-v2.2-a11-four-family` and its distinct preregistration receipt. This proves
producer reachability, not efficacy.

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
python3 -m unittest tests.test_a11_packet_adapter tests.test_a11_four_family_recipe
```

This gate removes the packet-rendering ambiguity that made the synthetic V
proxy insufficient. It does not remove the separate authorization and dataset
gates described in [`A11_EVENT_GROUP.md`](../prereg/A11_EVENT_GROUP.md) and
[`A11_SUBSTRATE_AUDIT.md`](A11_SUBSTRATE_AUDIT.md).
