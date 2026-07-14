# A11 pre-registration — path-required clinical context

Status: **pre-answer / zero model calls**
Protocol version: `a11-path-required-v1`
Mechanism fixture: `fixtures/a11_path_required_cases.json`

## Question

When the evidence needed to answer a clinical question is not present in a
patient-centered star packet, does bounded traversal of explicit FHIR
references improve answer correctness and evidence recall without increasing
authorization leakage, nondeterminism, latency, or token cost beyond the
registered limits?

This experiment evaluates a **logical context projection**. It does not test or
claim a graph database. The same traversal contract may be implemented over
canonical FHIR JSON, an edge projection in Postgres, or another storage engine
only if those implementations produce byte-equivalent governed packets.

## Why A11 is separate from QT-4

QT-4 tests a small set of micro-question paths in an existing benchmark that
is mostly a patient-centered star. A11 changes the benchmark topology: every
answerable question requires an explicit path of at least two hops from its
registered seed resource, and the terminal evidence is absent from the star
packet. This is the setting in which traversal can earn its complexity.

## Frozen mechanism gate (implemented now)

The committed synthetic fixtures contain no PHI and make no model calls. They
cover four answerable topologies and six fail-closed topologies:

- `DiagnosticReport -> Observation -> Device`
- `MedicationRequest -> Medication -> Substance`
- `CarePlan -> PractitionerRole -> Organization`
- `DiagnosticReport -> Specimen -> PractitionerRole -> Organization`
- a target with the same logical id in another practice
- an exact versioned target for which only a newer version is visible
- a deleted/missing target
- a purpose-denied request
- deterministic target-limit exhaustion
- deterministic model-visible packet-byte exhaustion

The cross-practice, stale-version, and deleted cases must produce the same
public unavailable state and path length; request-known target/path fields may
differ. The packet must not disclose whether a target exists outside the
authorization scope, existed at another version, or was deleted.

The gate is deterministic and must pass before any answer run:

1. Every answerable star packet lacks the registered terminal evidence.
2. Every answerable traversal packet contains it with a deterministic path
   citation of at least the registered depth.
3. Cross-practice, stale, and deleted targets never enter the packet.
4. Those three failure modes expose the same unavailable state and step count.
5. Repeated builds produce byte-identical packets, results, and manifests.
6. Vocabulary-plus-traversal has identical evidence recall to traversal and
   no larger packet for every fixture, with at least one strict byte reduction.
7. A purpose outside the registered allowlist returns no scoped resources.
8. Target and packet-byte bounds omit the forbidden terminal evidence and emit
   deterministic bound receipts without exceeding the byte budget.

Passing this gate licenses construction and preregistration of an efficacy
dataset; it is not itself evidence that model correctness improved, nor does it
waive the production compiler's ABAC and audit gates.

## Arms

All arms use identical questions, answer model, effort, prompt, output schema,
retry policy, grading, and authorization scope. Only packet construction
differs.

### S — patient star

Include the Patient, registered seed resource, and resources that directly
reference the Patient. Do not follow outbound references from those resources.

### T — bounded explicit traversal

Starting at the registered seed, follow only relative FHIR
`Reference.reference` values. Enforce the registered resource-type allowlist,
maximum depth, practice scope, purpose, maximum targets, and exact-version
semantics, plus a model-visible packet-byte limit. Emit deterministic
JSON-pointer path citations. Missing, stale, and out-of-scope targets all
become `unavailable`; purpose, target, and byte bounds fail closed.

### VT — vocabulary plus bounded traversal

Use the same walker, bounds, and seeds as T, but narrow the resource-type
allowlist using a frozen question-family vocabulary selected before retrieval.
This arm is included only because the fixture cleanly isolates vocabulary as a
filter over the same traversal. If the efficacy dataset cannot preserve that
isolation, VT will be dropped by amendment before any answers are generated.

## Efficacy dataset to freeze before model calls

Construct at least 120 non-PHI benchmark questions, balanced across at least
four path families and depths two and three. At least 20% must be registered
unanswerable safety cases, balanced across cross-practice, stale-version,
deleted-target, and bound-exhaustion conditions. No question may enter the
answerable set unless deterministic grading proves:

- the reference answer is supported by terminal evidence;
- S does not contain that terminal evidence;
- T contains it through the registered explicit path;
- the same answer is not recoverable from labels or metadata in S; and
- question, answer, patient, path family, and packet build all pass a
  duplicate/leakage audit.

Question selection, exclusions, split, arm order, all packet hashes, model pin,
Codex binary/version, and grading configuration must be sealed in one manifest
before the first answer. Answer content must not be inspected mid-run.

## Outcomes

### Primary

Paired answer correctness, **T minus S**, graded by the same pinned arm-blind
three-vote panel used by the current experiment program. Report counts,
percentage-point difference, paired bootstrap 95% interval, and exact McNemar
test. The confirmatory promotion criterion is a positive point estimate, a 95%
interval excluding zero, and zero critical safety failures.

### Registered secondary

- VT minus T paired correctness with the same uncertainty report.
- Deterministic terminal-evidence recall by arm and path family.
- Abstention, unsupported-answer, and wrong-evidence rates.
- Packet bytes and accepted input/output/total model tokens per answer.
- All-attempt tokens, retries, timeout/error counts, and accepted-attempt yield.
- Deterministic citation validity: every cited step exists in the sealed source
  version and reaches the cited terminal resource.
- Bounds outcomes: unavailable, depth limit, type filter, target limit, stale
  version, and authorization denial, without exposing which denial occurred.

Mechanism success in the zero-model fixture is never reported as answer
correctness.

## Latency and economic measurement

Packet compilation latency is measured separately from model latency so a fast
or slow answer provider cannot hide retrieval cost. On the pinned experiment
host and data snapshot:

- perform 20 unreported warmups and 100 measured builds per case/arm;
- run arms in a deterministic interleaved order;
- report median, p95, p99, database round trips, resources examined, edges
  examined, and packet bytes;
- retain raw monotonic-clock receipts but keep timing fields out of canonical
  packet hashes;
- compare query-time traversal and any materialized edge projection only after
  they pass a byte-equivalence and authorization-equivalence gate.

For model economics, derive accepted and all-attempt usage from each
`turn.completed.usage` receipt. Report input, cached input if exposed, output,
reasoning, and total tokens separately. Record the model pricing table and
effective date at analysis time; cost is derived from preserved tokens so it
can be recomputed when prices change. Panel tokens are recorded separately and
never folded into answer-arm token totals.

## Hard safety gates

The experiment fails regardless of correctness if any of the following occurs:

- a resource outside the registered practice/purpose scope appears in a packet;
- cross-practice, stale, and deleted cases reveal distinguishable existence
  information;
- an exact versioned reference silently resolves to another version;
- a path citation cannot be replayed against the sealed source snapshot;
- an arm exceeds its registered depth, type, or target bound;
- two builds from identical inputs produce different canonical bytes; or
- accepted/all-attempt token receipts cannot be reconciled.

## Interpretation boundary

A positive result supports building Bonfire's bounded traversal interface; the
interface is not called policy-aware until the production compiler independently
passes scope-before-retrieve, patient/consent, purpose, and audit gates. It does
not license the claim that a native graph store is better
than Postgres, that arbitrary graph traversal is safe, or that graph-shaped
context improves questions whose evidence is already in a star packet. A
storage-engine decision requires a separate byte-equivalent latency/cost study
under production-scale charts.

## Zero-model commands

```bash
python a11_path_required_benchmark.py \
  --fixture fixtures/a11_path_required_cases.json \
  --output-dir runs/a11-path-required-v1

python -m pytest -q tests/test_a11_path_required_benchmark.py
```

Neither command invokes an answer or judge model.
