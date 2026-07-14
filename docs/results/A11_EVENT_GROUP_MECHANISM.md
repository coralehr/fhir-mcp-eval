# A11 event-group zero-model mechanism result

Status: **complete mechanism gate; efficacy not run**

Completed: 2026-07-14

Protocol: [`A11_EVENT_GROUP.md`](../prereg/A11_EVENT_GROUP.md)

## Result

The hardened deterministic compiler passes all ten synthetic non-PHI
mechanism and safety cases. No answer or judge model was called.

| Arm | Mechanism successes | Answerable terminal recall | Scope leakage | Model packet bytes |
|---|---:|---:|---:|---:|
| V proxy: promoted-recipe-shaped star | 10/10 | 0/4 (0%) | 0 | 4,228 |
| T proxy: recipe-shaped flat traversal | 10/10 | 4/4 (100%) | 0 | 15,658 |
| E proxy: recipe-shaped event groups | 10/10 | 4/4 (100%) | 0 | 19,910 |

The fixture now covers two path families, first/latest selection, a missing
terminal, cross-practice evidence, an exact stale version, purpose denial, a
same-practice cross-patient seed, and a mixed exact-version path containing
both an available and a stale target. A tenth case rejects a same-practice
terminal that explicitly belongs to another patient.

The compiler:

- derives and seals its temporal/path plan from question text alone before
  consulting any gold or audit fields;
- requires every seed root to name the requested patient explicitly;
- fetches each canonical target at most once while retaining replayable paths;
- follows only four exact registered relation/pointer shapes and rejects
  nested-reference masquerades;
- caps discovery at 128 edges and path materialization at 256 citations;
- carries requested and resolved references separately for available exact
  versions;
- replaces the entire unavailable FHIR Reference object with a constant
  marker, removing reference, display, identifier, type, and extensions;
- ranks events only from supported FHIR clinical `effective[x]` fields;
- applies one fail-closed byte gate to V, T, and E; and
- produces byte-identical artifacts and manifests from identical inputs.

The event-group packet was 4,252 bytes (27.2%) larger than flat traversal on
this small fixture. That is a measured mechanism cost, not an efficiency win.
A11 must show that the structure improves correctness or downstream
token/retry economics before it earns promotion.

## Review findings resolved before publication

Fresh safety, performance, testing, and maintainability passes found real
protocol faults in the first implementation: the event plan could depend on a
gold path signature, seeds were only practice-scoped rather than
patient-scoped, exact-version targets were joined incorrectly, the V arm did
not share the packet-byte gate, `issued` was treated as clinical time, flat
packets exposed unavailable target IDs, and duplicate paths consumed the
resource budget. Two red-team passes then caught target-level cross-patient
leakage, Reference side-field leakage, unregistered/nested relation bypasses,
unbounded path fan-out, arm-label leakage on bound failure, and an E-side root
dependency outside T's retrieval receipt. The committed core fixes each issue
and adds regression cases. The historical `a11_path_required_benchmark.py`
remains unchanged; the hardened successor lives in the separately hashed
`a11_evidence_core.py`.

## What this does and does not establish

The gate establishes that the synthetic V/T/E-shaped comparison is executable
and that T and E proxies use an identical retrieval source, including
authorized root refs, before grouping. It reproduces the QT-4
failure shape: a selected latest event can be incomplete while an older event
is complete, so “gold exists somewhere in the packet” is not enough.

It does **not** consume actual `compile_evidence.py` packets and therefore does
not establish byte-equivalence to the promoted product recipe. It also does
not establish answer accuracy, model comprehension, generality to
non-microbiology tasks, production authorization, or an advantage for a
native graph store. Those require a sealed product-packet adapter and efficacy
dataset and, separately, a byte-equivalent storage/latency benchmark.

## Integrity receipt

- Fixture canonical-JSON SHA-256:
  `704d02857d565558f2ff6140e1abef22e9c8250996115f3f5f1800317184dec8`
- Fixture raw-file SHA-256:
  `62879ee5bc41bbab5185c6f16eca39b13ce4d77b838a9f9acac5dffa2d5253c2`
- Event compiler SHA-256:
  `04cdde2689edaacb7961bc8f83bbe1a18304bdfd670cbd48e087f6d554c1d041`
- Hardened evidence core SHA-256:
  `0069588dce48cc63bd9d78ff2369b4c3b1cda120ccdd7cf876c66c55a5132498`
- Mechanism result SHA-256:
  `cab8b3eaf48d58805e66af844be4df6d6dc9e48c8c5d0904412e19896cb81802`
- V JSONL SHA-256:
  `d2d6540446d682efb35e784e6b6654b24586bd75a80a3387d7d93b610b0951d0`
- T JSONL SHA-256:
  `a3964c8a47ccd44c8b843235264d465cb6bb60010679748e0abf1b668af7de15`
- E JSONL SHA-256:
  `c64e67fae4451aa4934cafc5ccba6bae2aac365aaad8fa9f8ffbbb722fcc3992`
- Model calls: `0`

Generated `runs/` artifacts remain gitignored. The committed fixture,
compiler, tests, hashes, and command reproduce them.

## Next gate

First bind synthetic V to byte-identical sealed `compile_evidence.py` packets.
The QT-4 packet inventory is also too small and topologically narrow for the
preregistered efficacy claim. See
[`A11_CANDIDATE_INVENTORY.md`](A11_CANDIDATE_INVENTORY.md). Do not launch
answer models until the adapter and a patient-disjoint, multi-family efficacy
dataset are constructed, audited, and sealed.
